from flask import Flask, send_file
from flask import jsonify, request
import boto3
from tempfile import NamedTemporaryFile
from extractor import Extractor
from pdf_parse.parser import BillParser
from bill_pricing import Bill
from best_offer import get_bests
import decimal
import json
from shutil import copyfile
import os
import requests
import stripe
from urllib import parse
from send_bill import send_ses_bill
from flask_cors import CORS
from datetime import datetime
import uuid
from smart_meter import get_history, RunAvg


SOURCE_BILL = os.environ.get("source-bill")

app = Flask(__name__)
CORS(app)
s3_resource = boto3.resource('s3')
dynamodb = boto3.resource('dynamodb')
best_offers_table = dynamodb.Table(os.environ.get('bests_offers_table'))
users_bill_table = dynamodb.Table(os.environ.get('users_bill_table'))

BILLS_BUCKET = os.environ.get('bills-bucket')
SWITCH_MARKINTELL_BUCKET = os.environ.get("switch-bucket")
BAD_BILLS_BUCKET = "ib-bad-bills"



def user_id():
    return f"anonymous-{uuid.uuid1()}"

def bill_id(priced):
    return (f"""{priced["users_nmi"]}_{priced["to_date"].replace("/","-")}""")

def bill_file_name(priced):
    return f"private/{user_id()}/{bill_id(priced)}.pdf"

def populate_bests_offers(bests, priced, nb_offers, ranking, key_file,customer_id):
    spot_date = datetime.today().strftime("%Y-%m-%d-%H-%M-%S")

    saving = -1;
    if len(bests):
        saving = bests[0]["saving"]
    tracking = {
        'avg_daily_use': priced["avg_daily_use"],
        'ranking': ranking,
        'evaluated': nb_offers,
        'saving': saving,
        'to_date': priced["to_date"]
    }
    priced = {k: v for k, v in priced.items() if v is not None}

    item = {
        'customer_id': customer_id,
        'source_bill': {'url': key_file},
        'bill_id_to_date': bill_id(priced),
        'spot_date': spot_date,
        'bests': bests,
        'priced': priced,
        'tracking': tracking
    }
    item = json.loads(json.dumps(item), parse_float=decimal.Decimal)
    print(item)
    if customer_id:
        best_offers_table.put_item(Item=item)

def populate_bill_users(bill, provider, customer_id, ip):
    sub = customer_id
    creation_date = datetime.today().strftime("%Y-%m-%d-%H-%M-%S")

    item = {
        "bill_id": bill["users_nmi"],
        "creation_date": creation_date,
        "to_date": bill["to_date"],
        "user_name": "anonymous_name",
        "user_email": "anonymous_email",
        "address": bill["address"],
        "bill_user_name": bill["name"],
        "region": bill["region"],
        "sub": sub,
        "provider":provider,
        "ip":ip
    }

    users_bill_table.put_item(Item=item)

def update_bests_offers(bests, priced, nb_offers, ranking):
    spot_date = datetime.today().strftime("%Y-%m-%d-%H-%M-%S")
    customer_id = user_id()

    saving = -1;
    if len(bests):
        saving = bests[0]["saving"]
    tracking = {
        'avg_daily_use': priced["avg_daily_use"],
        'ranking': ranking,
        'evaluated': nb_offers,
        'saving': saving,
        'to_date': priced["to_date"]
    }

    priced = json.loads(json.dumps(priced), parse_float=decimal.Decimal)
    bests = json.loads(json.dumps(bests), parse_float=decimal.Decimal)
    tracking = json.loads(json.dumps(tracking), parse_float=decimal.Decimal)
    key = {
        "bill_id_to_date": bill_id(priced),
        "customer_id": customer_id}
    best_offers_table.update_item(
        Key=key,
        UpdateExpression="set priced=:priced,tracking=:tracking,best=:bests,spot_date=:spot_date",
        ExpressionAttributeValues={':priced': priced,
                                   ':bests': bests,
                                   ':tracking': tracking,
                                   ':spot_date': spot_date},
        ReturnValues="UPDATED_NEW")

def bad_results(message, priced={}, file=None, file_name=None):


    def user_message(argument):
        switcher = {
            "embedded":"""
                    Your are supplied on an embedded network. 
                    Unfortunately you can not choose your supplier. 
                    We are sorry we can not be useful to you.
                  """,
            "no_parsing":"""
                    We are sorry we could not read your bill. 
                    Could you please check that it is an original PDF bill. 
                    If the problem is on our side, we will fix it and let you know 
                    if you have signed up with us
                  """,
            "bad_best_offers": """
                            We are sorry we could not parse your bill very well. 
                            Could you please check that it is an original PDF bill. 
                            If the problem is on our side, we will fix it and let you know 
                            if you have signed up with us
                          """
        }
        return switcher.get(argument, message)


    message = user_message(message)
    if file:
        s3_resource.Bucket(BAD_BILLS_BUCKET).upload_file(Filename=file, Key=file_name)
        user_ = {"user_name": "anonymous_name","user_email": "anonymous_email"}
        send_ses_bill(file, user_,message)

    return jsonify(
        {'bests': [],
         'evaluated': -1,
         'bill': priced,
         "message": message
         }), 200

class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, decimal.Decimal):
            if o % 1 > 0:
                return float(o)
            else:
                return int(o)
        return super(DecimalEncoder, self).default(o)

def process_upload_miswitch(event, context):
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
    sub = key.split('/')[1]
    file_bytes = open(local_file, 'rb').read()
    url_miswitch = "https://switch.markintell.com.au/api/pdf/pdf-to-json"
    r = requests.post(url_miswitch, files={'pdf': file_bytes},
                      data={"source": SOURCE_BILL,
                            "file_name": "_".join(key.split("/")[1:]),
                            "user_name": "anonymous_name",
                            "user_email": "anonymous_email"
                            })
    print("reponse miswitch", r)

def annomyze_offers(priced, offers):
    for i, x in enumerate(offers):
        if x["saving"] > 100:
            o = x["origin_offer"]
            tariff = o["tariff"]
            index = i + 1
            x["url"] = f"url_{index}"
            o["url"] = f"url_{index}"
            x["retailer"] = f"RETALIER{index}"
            o["retailer"] = f"RETALIER{index}"
            x["distributor"] = f"DISTRIBUTOR{index}"
            o["distributor"] = f"DISTRIBUTOR{index}"
            x["offer_id"] = f"OFFER_ID_{index}"
            o["offer_id"] = f"OFFER_ID_{index}"
            o["offer_name"] = f"OFFER_NAME_{index}"
            o["retailer_url"] = f"RETALIER_URL{index}"
            o["retailer_phone"] = f"PHONE{index}"

            if "eligibility" in o: del o["eligibility"]
            if "eligibility" in tariff: del tariff["eligibility"]

    return offers
    pass


def scanned_priced(pdf_file):
    ## s3_resource.Bucket(SWITCH_MARKINTELL_BUCKET).upload_file(Filename=id, Key=key_file)
    try:
        url_miswitch = "https://switch.markintell.com.au/api/pdf/scanned-bill"
        r = requests.post(url_miswitch, files={"pdf":pdf_file})
        if r.status_code == 200:
            parsed = json.loads(r.content)
            return True,  parsed
    except Exception as ex:
        print(ex)
        bad_message = "Sorry we could not automatically read your bill.\n Can you please make sure you have an original PDF and then try again."
        return False, f"This is a scanned bill.\n{bad_message}"


@app.route("/bests", methods=["POST"])
def bests():
    ip = request.remote_addr

    file_obj = request.files.get("pdf")
    pdf_data = file_obj.read()
    is_business = request.form.get("is_business")
    provider = request.form.get("provider")
    file_name = file_obj.filename
    print("trying to parse ...", file_name)
    id = f"/tmp/{uuid.uuid1()}.pdf"
    with NamedTemporaryFile("wb", suffix=".pdf", delete=False) as out:
        out.write(pdf_data)
        copyfile(out.name, id)
        is_bill, message = Extractor.check_bill(out.name)

        if not is_bill:
            if "scanned" in message:
                res, parsed = scanned_priced(pdf_data)
                if not res:
                    return bad_results(parsed, file=id, file_name=file_name)
            else:
                return bad_results(message, file=id, file_name=file_name)
        else:
            Extractor.process_pdf(out.name)
            bp = BillParser(
                xml_=Extractor.xml_,
                xml_data_=Extractor.xml_data,
                txt_=Extractor.txt_,
                file_name=file_name)
            try:
                bp.parse_bill()
            except Exception as ex:
                print(ex)
                return bad_results("no_parsing", file=id, file_name=file_name)
            if not bp.parser or not bp.parser.json:
                return bad_results("no_parsing", file=id, file_name=file_name)
            parsed = bp.parser.json

        priced = Bill(dict(parsed))()
        if priced.get("retailer"):
            if priced["retailer"] in ["winenergy","ocenergy","embeddedorigin"]:
                return bad_results("embedded")

        history = get_history('beatyourbill-bucket', parsed["users_nmi"])
        history.append(parsed)
        if history:
            runn = RunAvg(history).running_parameters()
            curr = RunAvg([parsed]).running_parameters()
            ann_factor = round(runn["run_avg_daily_use"] / curr["run_avg_daily_use"], 5)
            for k, v in parsed.items():
                if "_usage" in k:
                    parsed[k] = round(v * ann_factor)
            if parsed["has_solar"]:
                parsed["ann_solar_volume"] = runn["run_solar_export"]
            priced = Bill(dict(parsed))()



        try:
            res, nb_offers, nb_retailers, ranking = get_bests(priced, "", n=-1, is_business=is_business)
        except Exception as ex:
            print(ex)
            return bad_results("bad_best_offers", file=id, file_name=file_name)

        key_file = bill_file_name(priced)
        customer = user_id()
        populate_bill_users(priced,provider, customer, ip )
        populate_bests_offers(res, priced, nb_offers, ranking, key_file=key_file,customer_id=customer)
        s3_resource.Bucket(BILLS_BUCKET).upload_file(Filename=id, Key=key_file)
        ## s3_resource.Bucket(SWITCH_MARKINTELL_BUCKET).upload_file(Filename=id, Key=key_file)
        try:
            url_miswitch = "https://switch.markintell.com.au/api/pdf/pdf-to-json"
            r = requests.post(url_miswitch, files={'pdf': pdf_data},
                              data={"source": SOURCE_BILL,
                                    "file_name": "_".join(key_file.split("/")[1:]),
                                    "user_name": "anonymous_name",
                                    "user_email": "anonymous_email"
                                    })
            print("reponse miswitch", r)
        except Exception as ex:
            print(ex)
            

        os.remove(id)
        if not len(res):

            result = {
                "evaluated": nb_offers,
                "ranking": ranking,
                "nb_retailers": nb_retailers,
                "bests": res,
                "bill": priced,
                "message": "no saving"}
            result = json.dumps(result, indent=4)
            return result, 200
        res = annomyze_offers(priced, res)
        result = {
            "evaluated": nb_offers,
            "ranking": ranking,
            "nb_retailers": nb_retailers,
            "bests": res,
            "bill": priced,
            "message": "saving"}
        result = json.dumps(result, indent=4)
        return result, 200


