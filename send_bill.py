import os
import boto3
from botocore.exceptions import ClientError
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from zappa.asynchronous import task




@task
def send_feedback(bill_file,message,user_):

    SENDER = "Intelligibill <contact@ag-study.com>"
    RECIPIENT = ["bruce.mountain@cmeaustralia.com.au",
                 "contact@ag-study.com"]

    AWS_REGION = "us-east-1"
    SUBJECT = "User: feedback"
    ATTACHMENT = bill_file
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
    ## msg['To'] = RECIPIENT
    msg_body = MIMEMultipart('alternative')
    textpart = MIMEText(BODY_TEXT.encode(CHARSET), 'plain', CHARSET)
    msg_body.attach(textpart)
    msg.attach(msg_body)
    if ATTACHMENT:
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



@task
def send_ses_bill(bill_file,user_):

    SENDER = "Intelligibill <contact@ag-study.com>"
    RECIPIENT = ["bruce.mountain@cmeaustralia.com.au",
                 "contact@ag-study.com"]
    AWS_REGION = "us-east-1"
    SUBJECT = "Intelligibill:  bill parsing problem"
    ATTACHMENT = bill_file

    # The email body for recipients with non-HTML email clients.
    message = "Hi Bruce,\r\nPlease see what happened with the attached bill"

    BODY_TEXT = message

    # The HTML body of the email.
    BODY_HTML = f"""\
    <html>
    <head></head>
    <body>
    <h1>Hi!</h1>
    <p>{message}</p>
    <p>user: {user_["user_name"]}</p>
    <p>email: {user_["user_email"]}</p>
    </body>
    </html>
    """

    # The character encoding for the email.
    CHARSET = "utf-8"

    # Create a new SES resource and specify a region.
    client = boto3.client('ses',region_name=AWS_REGION)

    # Create a multipart/mixed parent container.
    msg = MIMEMultipart('mixed')
    # Add subject, from and to lines.
    msg['Subject'] = SUBJECT
    msg['From'] = SENDER
    ## msg['To'] = RECIPIENT

    # Create a multipart/alternative child container.
    msg_body = MIMEMultipart('alternative')

    # Encode the text and HTML content and set the character encoding. This step is
    # necessary if you're sending a message with characters outside the ASCII range.
    textpart = MIMEText(BODY_TEXT.encode(CHARSET), 'plain', CHARSET)
    htmlpart = MIMEText(BODY_HTML.encode(CHARSET), 'html', CHARSET)

    # Add the text and HTML parts to the child container.
    msg_body.attach(textpart)
    msg_body.attach(htmlpart)

    # Define the attachment part and encode it using MIMEApplication.
    att = MIMEApplication(open(ATTACHMENT, 'rb').read())

    # Add a header to tell the email client to treat this part as an attachment,
    # and to give the attachment a name.
    att.add_header('Content-Disposition','attachment',filename=os.path.basename(ATTACHMENT))

    # Attach the multipart/alternative child container to the multipart/mixed
    # parent container.
    msg.attach(msg_body)

    # Add the attachment to the parent container.
    msg.attach(att)
    #print(msg)
    try:
        #Provide the contents of the email.
        response = client.send_raw_email(
            Source=SENDER,
            Destinations= RECIPIENT,
            RawMessage={
                'Data':msg.as_string(),
            }
        )
    # Display an error if something goes wrong.
    except ClientError as e:
        print(e.response['Error']['Message'])
    else:
        print("Email sent! Message ID:"),
        print(response['MessageId'])
