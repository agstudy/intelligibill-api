import email
from urllib import parse
import requests
import boto3
import uuid
from bs4 import BeautifulSoup
from typing import List
import json

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from botocore.exceptions import ClientError
from send_bill import send_ses_bill
from tempfile import NamedTemporaryFile


s3_resource = boto3.resource('s3')


class SavingResult:
    retailer_name: str
    retailer_img: str
    retailer_link: str
    saving: int
    offer_total_bill: int


class EmailInput:
    username: str
    cta: str
    saving: int
    saving_results: List[SavingResult]
    email_body: str


def populateResult(el):
    row_content = f"""
<table align="left" border="0" cellpadding="0" cellspacing="0" style="width: 100%; min-width:425px">
    <tr>
    <td style="padding:16px">
        <table align="left" border="0" cellpadding="0" cellspacing="0" style="width: 100%;">
            <tr>
                <td>
                    <table width="100%" border="0" cellspacing="0" cellpadding="0">
                        <tr>
                            <td style="color:#75737a; font-family:sans-serif; font-size:16px; line-height:22px; border:1px solid #dfe5ea; border-radius:5px; height:80px; padding:10px" height="80">
                                <table width="100%" border="0" cellspacing="0" cellpadding="0">
                                    <tr>
                                        <td style="text-align: left; width: 70px;">
                                            <img width="60" height="auto" src="https://www.beatyourbill.com.au/assets/retailers/{el.retailer_img}.png" alt="{el.retailer_img}" style="height:auto">
                                        </td>
                                        <td style="width: 120px; font-size: 15px;">
                                            <table width="100%" border="0" cellspacing="0" cellpadding="0">
                                                <tr>
                                                    <td class="retailer" style="color: #2a34a5;">{el.retailer_name}</td>
                                                </tr>
                                                <tr>
                                                    <td>
                                                        <a style="font-size: 12px; 
                                          text-decoration: none; 
                                          color: rgb(151, 150, 155);" class="retailer_link" href="${el.retailer_link}">
                                            view details
                                          </a>
                                                    </td>
                                                </tr>
                                            </table>
                                        </td>
                                        <td style="text-align: right;">
                                            <table width="100%" border="0" cellspacing="0" cellpadding="0">
                                                <tr style="font-size: 24px; color: #1850ca; text-align: right;">
                                                    <td class="annuel_saving"> ${el.saving} </td>
                                                </tr>
                                                <tr style="font-size: 14px; line-height: 16px; text-align: right;">
                                                    <td style="padding-top: 6px; color: #2a34a5;">
                                                        Annual saving
                                                    </td>
                                                </tr>
                                            </table>
                                        </td>
                                        <td style="text-align: right;">
                                            <table width="100%" border="0" cellspacing="0" cellpadding="0">
                                                <tr style="font-size: 24px; color: rgb(24, 80, 202); text-align: right;">
                                                    <td class="annnuel_estimated"> ${el.offer_total_bill} </td>
                                                </tr>
                                                <tr style="font-size: 14px; line-height: 16px; text-align: right;">
                                                    <td style="padding-top: 6px;  color: #2a34a5;">
                                                        Estimated Annual Bill
                                                    </td>
                                                </tr>
                                            </table>
                                        </td>
                                    </tr>
                                </table>
                            </td>
                        </tr>
                    </table>
                </td>
            </tr>
        </table>
    </td>
</tr>
</table>"""
    return BeautifulSoup(row_content, 'html.parser')


def populateHeader(saving, text=None):
    savingText = f"""
    <td  style="color:#75737a; font-family:sans-serif; font-size:16px; line-height:22px">
        Based on current usage, we've found you can save 
        <span style="color:#4a4f89; font-family:sans-serif; font-size:24px; font-weight:bold; line-height:18px; padding:0 0 15px 0">
                                    ${saving} </span> a year by
        switching to a new provider. Find a better electricity deal
        and get more money to spend on the things that matter.
    </td>
    """
    noSavingText = f"""
    <td  style="color:#75737a; font-family:sans-serif; font-size:16px; line-height:22px">{text}</td>
    """

    if saving:
        return BeautifulSoup(savingText, 'html.parser')

    return BeautifulSoup(noSavingText, 'html.parser')


def create_email(r):
    ei = EmailInput()

    ei.username = r["bill"]["name"]
    ei.cta = "Join Now and Save!"
    if r["bests"]:
        ei.saving = r["bests"][0]["saving"]
        ei.saving_results = []
        for x in r["bests"]:
            sr = SavingResult()
            sr.retailer_name = x["retailer"]
            sr.retailer_link = x["retailer_url"]
            sr.offer_total_bill = x["offer_total_bill"]
            sr.saving = x["saving"]
            sr.retailer_img = x["retailer"].lower().split(" ", 1)[0]
            if "retalier" in x["retailer"].lower():
                sr.retailer_img = "retalier"
                sr.retailer_name.replace("etalier", "etailer")

            ei.saving_results.append(sr)
        with open("email.html") as template:
            txt = template.read()
            soup = BeautifulSoup(txt, 'html.parser')
            header_text = soup.find(class_="header_text")
            header_text.append(populateHeader(ei.saving))
            result_row = soup.find(class_="result_row")

        for el in ei.saving_results:
            result_row.append(populateResult(el))
        user_name = soup.find(class_="username")
        user_name.append(BeautifulSoup(ei.username, "html.parser"))
        cta_message = soup.find(class_="cta_message")
        cta_message.clear()
        cta_message.append(BeautifulSoup(ei.cta, "html.parser"))

        ei.email_body = str(soup)
        return ei


def send_result_email(to, body_html, saving):
    SENDER = "BeatYourBill <contact@beatyourbill.com.au>"
    RECIPIENT = [to]
    AWS_REGION = "us-east-1"
    SUBJECT = f"Save ${saving} a year off your electricity bill"

    BODY_HTML = body_html

    CHARSET = "utf-8"

    client = boto3.client('ses', region_name=AWS_REGION)

    msg = MIMEMultipart('mixed')
    msg['Subject'] = SUBJECT
    msg['From'] = SENDER
    msg_body = MIMEMultipart('alternative')
    htmlpart = MIMEText(BODY_HTML.encode(CHARSET), 'html', CHARSET)
    msg_body.attach(htmlpart)
    msg.attach(msg_body)
    try:
        response = client.send_raw_email(
            Source=SENDER,
            Destinations=RECIPIENT,
            RawMessage={
                'Data': msg.as_string(),
            }
        )
    except ClientError as e:
        print(e.response['Error']['Message'])
    else:
        print("Email sent! Message ID:"),
        print(response['MessageId'])


def parse_send_email(file_name):
    with open(file_name) as f:
        msg = email.message_from_file(f)
        From = msg["from"].strip()
        attachments = msg.get_payload()
        for attachment in attachments:
            try:
                file_bytes = attachment.get_payload(decode=True, )
                bill_name = attachment.get_filename()
                print("email attachment bill is ", bill_name)
                if bill_name:
                    byb_url = "https://free.beatyourbill.com.au/bests"
                    r = requests.post(byb_url, files={'pdf': file_bytes})
                    res = json.loads(r.content)
                    msg = res.get('message')
                    if msg == "saving":
                        ei = create_email(res)
                        send_result_email(From, ei.email_body, ei.saving)
                    else:
                        id = f"/tmp/{uuid.uuid1()}.pdf"
                        with NamedTemporaryFile("wb", suffix=".pdf", delete=False) as out:
                            out.write(file_bytes)
                            user_ = {"user_name": From, "user_email": From}
                            send_ses_bill(out.name, user_, msg, to= [From])
            except Exception as detail:
                print(detail)
                pass


def process_new_email(event, context):
    """
    Process a file upload.
    """
    x = event['Records'][0]
    bucket = x['s3']['bucket']['name']
    key = x['s3']['object']['key']
    key = parse.unquote_plus(key)
    id = uuid.uuid1()
    local_file = f"/tmp/{id}.pdf"
    s3_resource.Bucket(bucket).download_file(Filename=local_file, Key=key)
    parse_send_email(local_file)


if __name__ == '__main__':
    file_name = "/home/agstudy/Downloads/2rkih1mue32bif2llctqf1k1470946svucbobeo1"

    ## file_name = "/home/agstudy/Downloads/example_mail"
    parse_send_email(file_name)
    pass
