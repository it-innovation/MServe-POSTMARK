# Do POSTMark setup
POSTMARK = False
if POSTMARK:
    CELERY_IMPORTS += ("postmark.tasks",)
    INSTALLED_APPS += ('postmark',)
    # POSTMARK SPECIFIC
    DIGITAL_RAPIDS_INPUT_DIR = "/mnt/postmark/postmark/input/QuickTime_H264_768x576_2Mbps_AAC_192Kbps_Stereo/"
    DIGITAL_RAPIDS_OUTPUT_DIR = "/mnt/postmark/postmark/output/QuickTime_H264_768x576_2Mbps_AAC_192Kbps_Stereo/"

    FIMS_HOST='http://localhost:8000/'

    if DEBUG:
        FIMS_HOST='http://localhost:8000/'

    FIMS_MEWS_URL_TRANSFORM=FIMS_HOST+'transformrequest/'
    FIMS_MEWS_URL_JOBQUERY=FIMS_HOST+'queryjobrequest/'
    FIMS_MEWS_URL_MANAGE=FIMS_HOST+'manageJobRequest/'
    FIMS_MEWS_MOUNT='mount_path'

    R3D_HOST="r3dhost"
    R3D_USER="r3dusername"
    R3D_PASS="r3dpassword"
    R3D_MOUNT="/Volumes/ifs/mserve/"

