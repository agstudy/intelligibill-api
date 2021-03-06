import os
import boto3
from botocore.exceptions import ClientError
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from zappa.asynchronous import task
from shutil import copyfile
import json, uuid
from tempfile import NamedTemporaryFile


s3_resource = boto3.resource('s3')
BILLS_BUCKET = os.environ.get('bills-bucket')



feedback_list = os.environ.get('feedback_list')


@task
def send_feedback(bill_file,message,user_):

    SENDER = "BeatYourBill <contact@ag-study.com>"
    RECIPIENT = feedback_list.split(',')

    AWS_REGION = "us-east-1"
    SUBJECT = "User: feedback"
    message = f"""
        {message}
        user: {user_["user_name"]}
        email: {user_["user_email"]} 
    """
    BODY_TEXT = message
    CHARSET = "utf-8"
    client = boto3.client('ses',region_name=AWS_REGION)
    msg = MIMEMultipart('mixed')
    msg['Subject'] = SUBJECT
    msg['From'] = SENDER
    msg_body = MIMEMultipart('alternative')
    textpart = MIMEText(BODY_TEXT.encode(CHARSET), 'plain', CHARSET)
    msg_body.attach(textpart)
    msg.attach(msg_body)
    if bill_file:
        att = MIMEApplication(open(bill_file, 'rb').read())
        att.add_header('Content-Disposition','attachment',filename=os.path.basename(bill_file))
        msg.attach(att)
    try:
        response = client.send_raw_email(
            Source=SENDER,
            Destinations= RECIPIENT,
            RawMessage={
                'Data':msg.as_string(),
            }
        )
    except ClientError as e:
        print(e.response['Error']['Message'])
    else:
        print("Email sent! Message ID:"),
        print(response['MessageId'])



@task
def send_ses_bill(bill_file,user_,user_message, to=None , upload_id=None, error = None ):
    SENDER = "BeatYourBill <contact@beatyourbill.com.au>"
    AWS_REGION = "us-east-1"
    if user_=="anonymous":
        user_ = {"user_name": "anonymous_name", "user_email": "anonymous_email"}

    if to:
        RECIPIENT = to
    else:
        RECIPIENT = feedback_list.split(',')
    SUBJECT = "BeatYourBill:  bill parsing problem"

    message = "Hi ,\r\nPlease see what happened with the attached bill"
    error_message = ""
    if error:
        error_message = f"There is a validation error. Parsed value of {error} is out of limits"

    BODY_HTML = f"""\
    <html>
    <head></head>
    <body>
    <h1>Hi!</h1>
    <p>{message}</p>
    <p>user: {user_["user_name"]}</p>
    <p>email: {user_["user_email"]}</p>
    <p>Message sent to user: {user_message}</p>
    <p>{error_message}</p>
    </body>
    </html>
    """
    if to :
        BODY_HTML = f"""\
        <html>
        <head></head>
        <body>
        <h1>Hi!</h1>
        <p>{user_message}</p>
        </body>
        </html>
        """

    CHARSET = "utf-8"

    client = boto3.client('ses',region_name=AWS_REGION)

    msg = MIMEMultipart('mixed')
    msg['Subject'] = SUBJECT
    msg['From'] = SENDER
    msg_body = MIMEMultipart('alternative')
    htmlpart = MIMEText(BODY_HTML.encode(CHARSET), 'html', CHARSET)
    msg_body.attach(htmlpart)
    msg.attach(msg_body)
    if upload_id:
        bill_file = f"/tmp/{upload_id}.pdf"
        key_file = f"upload/{upload_id}.pdf"
        s3_resource.Bucket(BILLS_BUCKET).download_file(Filename=bill_file, Key=key_file)

    if bill_file:
        ATTACHMENT = bill_file
        att = MIMEApplication(open(ATTACHMENT, 'rb').read())
        att.add_header('Content-Disposition','attachment',filename=os.path.basename(ATTACHMENT))
        msg.attach(att)
    try:
        response = client.send_raw_email(
            Source=SENDER,
            Destinations= RECIPIENT,
            RawMessage={
                'Data':msg.as_string(),
            }
        )
    except ClientError as e:
        print(e.response['Error']['Message'])
    else:
        print("Email sent! Message ID:"),
        print(response['MessageId'])


def receive_feedback(comment, file_obj,user_):
    if not file_obj:
        send_feedback(message=comment, user_=user_, bill_file=None)
        result = {"feedback": True}
        result = json.dumps(result, indent=4)
        return result, 200

    pdf_data = file_obj.read()
    id_file = f"/tmp/{uuid.uuid1()}.pdf"
    with NamedTemporaryFile("wb", suffix=".pdf", delete=False) as out:
        out.write(pdf_data)
        copyfile(out.name, id_file)
        send_feedback(id_file, comment, user_)
        result = {"feedback": True}
        result = json.dumps(result, indent=4)
        return result, 200



def contact_message(message, email):
    user_ = {"user_name": email,     "user_email": email}
    send_feedback(bill_file= None, message = message, user_= user_)
    result = {"feedback": True}
    result = json.dumps(result, indent=4)
    return result, 200

if __name__ == '__main__':
    contact_message(
        message= "amine gassem tests",
        email="amingassem@gmail.com"
    )
