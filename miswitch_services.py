import requests
import json
import boto3
from cts import SOURCE_BILL, BILLS_BUCKET

s3_resource = boto3.resource('s3')

def ocr_scanned(pdf_file):
    try:
        url_miswitch = "https://switch.markintell.com.au/api/pdf/scanned-bill"
        with open(pdf_file, "rb") as f:
            r = requests.post(url_miswitch, files={"pdf": f})
            if r.status_code == 200:
                parsed = json.loads(r.content)
                return True, parsed
            return False, None
    except Exception as ex:
        print(ex)
        bad_message = "Sorry we could not automatically read your bill.\n Can you please make sure you have an original PDF and then try again."
        return False, f"This is a scanned bill.\n{bad_message}"

def _process_upload_miswitch(upload_id, email = None ):
    user_email = "anonymous_email"
    user_name = "anonymous_name"
    if email:
        user_email = email
        user_name = email
    local_file = f"/tmp/{upload_id}.pdf"
    key_file = f"upload/{upload_id}.pdf"
    s3_resource.Bucket(BILLS_BUCKET).download_file(Filename=local_file, Key=key_file)
    file_name = f"/{upload_id}.pdf"
    try:
        with open(local_file, "rb") as f:
            url_miswitch = "https://switch.markintell.com.au/api/pdf/pdf-to-json"
            r = requests.post(url_miswitch, files={'pdf': f},
                              data={"source": SOURCE_BILL,
                                    "file_name": file_name,
                                    "user_name": user_name,
                                    "user_email": user_email
                                    })
            print("reponse miswitch", r)
    except Exception as ex:
        print(ex)