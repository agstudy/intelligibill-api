import uuid, io, boto3
from cts import BILLS_BUCKET
from flask import  send_file
s3_resource = boto3.resource('s3')



def send_bill_page(bill_url, page, user_id):
    key = f"private/{user_id}/{bill_url}"
    key_image = f"""{key.replace(".pdf", "")}-page-{page}.png"""
    file_name = f"/tmp/{uuid.uuid1()}.png"
    s3_resource.Bucket(BILLS_BUCKET).download_file(Filename=file_name, Key=key_image)
    with open(file_name, 'rb') as bites:
        return send_file(
            io.BytesIO(bites.read()),
            attachment_filename=key_image,
            mimetype='image/png'
        )


def send_bill_pdf(bill_url , user_id):
    key = f"private/{user_id}/{bill_url}"
    file_name = f"/tmp/{uuid.uuid1()}.pdf"
    s3_resource.Bucket(BILLS_BUCKET).download_file(Filename=file_name, Key=key)
    with open(file_name, 'rb') as bites:
        return send_file(
            io.BytesIO(bites.read()),
            attachment_filename=bill_url,
            mimetype='application/pdf'
        )
