########################################################################
#
# University of Southampton IT Innovation Centre, 2011
#
# Copyright in this library belongs to the University of Southampton
# University Road, Highfield, Southampton, UK, SO17 1BJ
#
# This software may not be used, sold, licensed, transferred, copied
# or reproduced in whole or in part in any manner or form or in or
# on any media by any person other than in accordance with the terms
# of the Licence Agreement supplied with the software, or otherwise
# without the prior written consent of the copyright owners.
#
# This software is distributed WITHOUT ANY WARRANTY, without even the
# implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
# PURPOSE, except where stated in the Licence Agreement supplied with
# the software.
#
#	Created By :			Mark McArdle
#	Created Date :			2011-07-26
#	Created for Project :		Postmark
#
########################################################################
from celery.task import task
import pycurl
import logging
import os
import re
import tempfile
import datetime
import shutil
import os.path
import time
import dataservice.utils as utils
import settings as settings
import paramiko
import uuid
import tarfile
import zipfile
import simplejson as json
from django.core.files import File
from django.core.files.uploadedfile import SimpleUploadedFile
from ssh import MultiSSHClient
from dataservice.models import MFile
from jobservice.models import JobOutput
from django.shortcuts import render_to_response

def tar_files(temp_tarfile, base_dir, files):
    tar = tarfile.open(temp_tarfile, "w:gz")
    for name in files:
        fname = os.path.join(base_dir, name)
        aname = os.path.relpath(fname, base_dir)
        tar.add(fname, arcname=aname)
    tar.close()

def zip_files(temp_zipfile, base_dir, files):
    zip = zipfile.ZipFile(temp_zipfile, mode="w")
    for name in files:
        fname = os.path.join(base_dir, name)
        aname = os.path.relpath(fname, base_dir)
        zip.write(fname,arcname=aname,compress_type=zipfile.ZIP_DEFLATED)
    zip.close()

def get_files(directory, prefix=None, suffix=None, after=None):
    _files = []
    for fname in os.listdir(directory):
        file = os.path.join(directory, fname)
        if not os.path.isfile(file):
            continue
        if after:
            mtime = os.path.getmtime(file)
            if datetime.datetime.fromtimestamp(mtime) <= after:
                continue
        if prefix:
            if not re.match(prefix, fname):
                continue
        if suffix:
            if not re.search(suffix + "$", fname):
                continue
        _files.append(file)
    return _files


def _ssh_r2d(files, export_type, tmp_folder, start_frame=1, end_frame=2):
    ssh = MultiSSHClient()
    result = {}

    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(settings.R3D_HOST, username=settings.R3D_USER, password=settings.R3D_PASS)

    for file in files:
        test_filename = file.lower()
        if (test_filename.endswith(".r3d")):
            command = "redline --i %s --outDir %s --exportPreset %s" % (file, tmp_folder, export_type)
            if (start_frame > -1):
                command += " -s %s" % (start_frame)
            if (end_frame > -1):
                command += " -e %s" % (end_frame)
            logging.info(command)

            stdin, stdout, stderr = ssh.exec_command(command)
            result[file] = {}
            result[file]["stdout"] = stdout.readlines()
            result[file]["sdterr"] = stderr.readlines()

    return result

def _mfile_to_mount(remote_mount, mf):
    local_mount = settings.STORAGE_ROOT
    if mf.service.container.default_path:
        local_mount = os.path.join(settings.STORAGE_ROOT, mf.service.container.default_path)
    mf_local_path = mf.file.path
    mf_remote_path = mf_local_path.replace(local_mount,remote_mount)
    return mf_remote_path

@task
def red2dtranscode(inputs, outputs, options={}, callbacks=[]):
    ''' Perform a 2D transcode using the redline tool '''
    started = datetime.datetime.now()

    logging.info("Inputs %s" % (inputs))
    if len(inputs) > 0: 
        input_mfile = MFile.objects.get(id=inputs[0])

        start_frame = -1
        if "start_frame" in options:
            start_frame = options["start_frame"]

        end_frame = -1
        if "end_frame" in options:
            end_frame = options["end_frame"]

        export_type = "tiff"
        if "export_type" in options and options["export_type"] != "":
            export_type = options["export_type"]

        # Take the input MFile and get all associated files to operate on - temp fix until we can update the UI for folder structures
        input_mfiles = [input_mfile]
        if not input_mfile.folder is None:
            input_mfiles = input_mfile.folder.mfile_set.all()

        inputs = [ _mfile_to_mount(settings.R3D_MOUNT, mf) for mf in input_mfiles]
        remote_mount = settings.R3D_MOUNT
        local_mount = settings.STORAGE_ROOT
        if input_mfile.service.container.default_path:
            local_mount = os.path.join(settings.STORAGE_ROOT, input_mfile.service.container.default_path)
        temp_folder_uuid = "r2d-transcode-" + str(uuid.uuid4())
        remote_image = os.path.join(remote_mount, temp_folder_uuid)
        local_image = os.path.join(local_mount, temp_folder_uuid)

        for mf_in in inputs:
            file_path = input_mfile.file.path
            logging.info("Selected R2D input file %s" % mf_in)
            logging.info("Processing file_path %s" % file_path)
            if input_mfile.service.container.default_path:
                logging.info("file_local_mount %s" % local_mount)
                file_relative = os.path.join(settings.R3D_MOUNT, input_mfile.service.container.default_path)
                logging.info("file_remote_mount %s" % file_relative)
            logging.info("remote_image %s" % remote_image)
            logging.info("local_image %s" % local_image)

        result = _ssh_r2d(inputs, export_type, remote_image, start_frame=start_frame, end_frame=end_frame)

        files = os.listdir(local_image)
        temp_archive = tempfile.NamedTemporaryFile('wb')
        zip_files(temp_archive.name, local_image, files)
        output_file = open(temp_archive.name, 'r')

        logging.info("output_file %s", output_file)

        if output_file:
            suf = SimpleUploadedFile("mfile", output_file.read(), content_type='application/octet-stream')

            if len(outputs) > 0:
                jo = JobOutput.objects.get(id=outputs[0])
                jo.file.save('results.zip', suf, save=True)
            else:
                logging.error("Nowhere to save output")

            output_file.close()
        else:
            raise Exception("Unable to get output_file location")

        return {"message": "R2D successful"}
    else:
        logging.error("No input given")
    raise


def _ssh_r3d(left_eye_file, right_eye_file, tmpimage, start_frame=1, end_frame=2):
    ssh = MultiSSHClient()

    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(settings.R3D_HOST, username=settings.R3D_USER, password=settings.R3D_PASS)
    clip_settings_file = "/Volumes/Media/masterRMD"
    output_file_dir, output_file_name = os.path.split(tmpimage)

    command = "redline -i3d %s %s -outDir %s -o %s --exportPreset 3Dtiff --makeSubDir --s  %s --e  %s --masterRMDFolder %s " % (
    left_eye_file, right_eye_file, output_file_dir, output_file_name, start_frame, end_frame, clip_settings_file)

    logging.info(command)

    stdin, stdout, stderr = ssh.exec_command(command)

    result = {}
    result["stdout"] = stdout.readlines()
    result["sdterr"] = stderr.readlines()

    return result


@task
def red3dmux(inputs, outputs, options={}, callbacks=[]):
    logging.info("Inputs %s" % (inputs))
    if len(inputs) > 0:
        left = MFile.objects.get(id=inputs[0])
        logging.info("Left %s" % left)
        if len(inputs) > 1:
            right = MFile.objects.get(id=inputs[1])
            logging.info("Right %s" % right)
            remote_mount = "/Volumes/ifs/mserve/"

            start_frame = 1
            if "start_frame" in options:
                start_frame = options["start_frame"]

            end_frame = 2
            if "end_frame" in options:
                end_frame = options["end_frame"]

            left_local_mount = os.path.join(settings.STORAGE_ROOT, left.service.container.default_path)
            right_local_mount = os.path.join(settings.STORAGE_ROOT, right.service.container.default_path)

            left_path = left.file.path
            right_path = right.file.path

            left_relative = left_path.replace(left_local_mount, remote_mount)
            right_relative = right_path.replace(right_local_mount, remote_mount)

            tfile_uuid = "r3d-image-" + str(uuid.uuid4())
            remoteimage = os.path.join(remote_mount, tfile_uuid)

            localimage = os.path.join(settings.STORAGE_ROOT, left.service.container.default_path, tfile_uuid,
                tfile_uuid + ".000004.tif")

            logging.info("left_local_mount %s" % left_local_mount)
            logging.info("right_local_mount %s" % right_local_mount)
            logging.info("left_path %s" % left_path)
            logging.info("right_path %s" % right_path)
            logging.info("left_relative %s" % left_relative)
            logging.info("right_relative %s" % right_relative)
            logging.info("remoteimage %s" % remoteimage)
            logging.info("localimage %s" % localimage)

            result = _ssh_r3d(left_relative, right_relative, remoteimage, start_frame=start_frame, end_frame=end_frame)

            logging.info(result)

            # TODO: Change to local in deployment
            outputfile = open(localimage, 'r')
            #outputfile = open(remoteimage,'r')

            suf = SimpleUploadedFile("mfile", outputfile.read(), content_type='application/octet-stream')

            if len(outputs) > 0:
                jo = JobOutput.objects.get(id=outputs[0])
                jo.file.save('image.tiff', suf, save=True)
            else:
                logging.error("Nowhere to save output")

            outputfile.close()

            return {"message": "R3D successful"}
        else:
            logging.error("No right eye input given")
    else:
        logging.error("No left eye input given")
    raise


def _drop_folder(filepath, inputfolder, outputfolder):
    try:
        uid = utils.unique_id()
        name = "%s_%s" % (uid, os.path.basename(filepath))
        inputfile = os.path.join(inputfolder, name)
        outputfile = os.path.join(outputfolder, name)
        shutil.copy(filepath, inputfile)

        exists = os.path.exists(outputfile)

        # Wait till file exists
        while not exists:
            logging.info("Output file %s doesnt exist, sleeping" % outputfile)
            time.sleep(10)
            exists = os.path.exists(outputfile)

        # Wait until file stops growing
        growing = True
        size = os.path.getsize(outputfile)
        logging.info("Size is %s" % str(size))
        while growing:
            time.sleep(10)
            newsize = os.path.getsize(outputfile)
            logging.info("Size is now %s" % str(newsize))
            growing = (size != newsize)
            size = newsize
            logging.info("File Growing  %s" % growing)

        return outputfile


    except Exception as e:
        logging.info("Error with watching drop folder %s" % e)
        raise e


@task
def digitalrapids(inputs, outputs, options={}, callbacks=[]):
    baseinputdir = settings.DIGITAL_RAPIDS_INPUT_DIR
    baseoutputdir = settings.DIGITAL_RAPIDS_OUTPUT_DIR

    inputdir = os.path.join(baseinputdir)
    outputdir = os.path.join(baseoutputdir)

    try:
        mfileid = inputs[0]
        joboutput = outputs[0]
        from dataservice.models import MFile

        mf = MFile.objects.get(id=mfileid)
        videopath = mf.file.path

        logging.info("Processing video Digital Rapids %s" % (videopath))
        if not os.path.exists(videopath):
            logging.info("Video %s does not exist" % (videopath))
            return False

        video = _drop_folder(videopath, inputdir, outputdir)

        from dataservice import usage_store

        inputfilesize = os.path.getsize(videopath)
        usage_store.record(mfileid, "http://mserve/digitalrapids", inputfilesize)

        videofile = open(video, 'r')

        from jobservice.models import JobOutput

        jo = JobOutput.objects.get(id=joboutput)
        jo.file.save('transcode.mov', File(videofile), save=True)

        videofile.close()

        return {"success": True, "message": "Digital Rapids transcode of video successful"}

    except Exception as e:
        logging.info("Error with digitalrapids %s" % e)
        raise e


class RequestReader:
    def __init__(self, tmpl, vars):
        self.finished = False
        self.POSTSTRING = str(render_to_response(tmpl, vars,
            mimetype='text/xml'))

    def read_cb(self, size):
        assert len(self.POSTSTRING) <= size
        if not self.finished:
            self.finished = True
            return self.POSTSTRING
        else:
            # Nothing more to read
            return ""

    def __len__(self):
        return  len(self.POSTSTRING)


class ResponseWriter:
    def __init__(self):
        self.contents = ''

    def body_callback(self, buf):
        self.contents = self.contents + buf

def fims_request(url, template, vars):
    request_reader = RequestReader(template, vars)
    response_writer = ResponseWriter()

    c = pycurl.Curl()
    c.setopt(c.URL, url)
    c.setopt(c.POST, 1)
    c.setopt(c.POSTFIELDSIZE, len(request_reader))
    c.setopt(c.READFUNCTION, request_reader.read_cb)
    c.setopt(c.WRITEFUNCTION, response_writer.body_callback)
    if settings.DEBUG:
        c.setopt(c.VERBOSE, 1)
    c.perform()
    status = c.getinfo(c.HTTP_CODE)
    if status >= 400:
        raise Exception("FIMS MEWS service returned an error %s " % status)
    c.close()

    response_js = json.loads(response_writer.contents)
    return response_js

def fims_job_status_request(url):
    job_status_response_writer = ResponseWriter()
    c = pycurl.Curl()
    c.setopt(c.URL, url)
    c.setopt(c.WRITEFUNCTION, job_status_response_writer.body_callback)
    if settings.DEBUG:
        c.setopt(c.VERBOSE, 1)
    c.perform()
    status = c.getinfo(c.HTTP_CODE)
    if status >= 400:
        raise Exception("FIMS MEWS service returned an error %s " % status)
    c.close()
    status_js = json.loads(job_status_response_writer.contents)
    return status_js

@task
def fims_mews(inputs, outputs, options={}, callbacks=[]):
    ''' Invokes the FIMS_MEWS service on remote mounted MServe storage '''

    try:
        mfileid = inputs[0]
        joboutput = outputs[0]
        from dataservice.models import MFile
        mf = MFile.objects.get(id=mfileid)

        # Using the code below we need to mount the raw MServe storage on the fims mews machine.
        bm_content_locator = _mfile_to_mount(settings.FIMS_MEWS_MOUNT, mf)
        job_uid = utils.unique_id()
        local_mount = settings.STORAGE_ROOT
        if mf.service.container.default_path:
            local_mount = os.path.join(settings.STORAGE_ROOT, mf.service.container.default_path)
        transfer_destination = os.path.join(local_mount,job_guid)

        transform_vars = {'bm_content_locator': bm_content_locator,
                          'job_uid': job_uid,
                          'transfer_destination': transfer_destination,
                          'output_wrapper': inputs[1],
                          'output_codec': inputs[2]}

        job_returned_js = fims_request(settings.FIMS_MEWS_URL_TRANSFORM, "transformRequest.xml", transform_vars)
        job_returned_uid = job_returned_js["transformAck"]["operationInfo"]["jobID"]["jobGUID"]

        status_returned_js = fims_job_status_request(settings.FIMS_MEWS_URL_JOBQUERY + job_returned_uid)
        status = status_returned_js["queryJobRequest"]["queryJobInfo"]["jobInfo"]["status"]["code"]

        while status != "completed":
            if status == "failed":
                # Failed job
                raise Exception("FIMS MEWS task failed")
            elif status == "canceled":
                # Canceled job
                raise Exception("FIMS MEWS task canceled")
            else:
                time.sleep(5)
                status_returned_js = fims_job_status_request(settings.FIMS_MEWS_URL_JOBQUERY + job_returned_uid)
                print status_returned_js
                status = status_returned_js["queryJobRequest"]["queryJobInfo"]["jobInfo"]["status"]["code"]

        files = os.listdir(transfer_destination)
        temp_archive = tempfile.NamedTemporaryFile('wb')
        zip_files(temp_archive.name, transfer_destination, files)
        output_file = open(temp_archive.name, 'r')

        logging.info("output_file %s", output_file)

        if output_file:
            suf = SimpleUploadedFile("mfile", output_file.read(), content_type='application/octet-stream')

            if len(outputs) > 0:
                jo = JobOutput.objects.get(joboutput)
                jo.file.save('results.zip', suf, save=True)
            else:
                logging.error("Nowhere to save output")

            output_file.close()
        else:
            raise Exception("Unable to get output_file location")

        # Clean up
        clean_up_vars = {'job_uid': job_returned_uid,
                         'command': "cleanup"}
        clean_up_returned_js = fims_request(settings.FIMS_MEWS_URL_MANAGE, "manageJobRequest.xml", clean_up_vars)

        return status_returned_js

    except Exception as e:
        logging.info("Error with FIMS_MEWS %s" % e)
        raise e
