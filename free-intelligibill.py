from flask import Flask, send_file
from flask import jsonify, request, redirect
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
from shared import annomyze_offers, bill_id, populate_bill_users, populate_bests_offers, copy_object

from flask_request_id_header.middleware import RequestID

SOURCE_BILL = os.environ.get("source-bill")

app = Flask(__name__)
app.config['REQUEST_ID_UNIQUE_VALUE_PREFIX'] = 'open-'

RequestID(app)

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


def bill_file_name(priced):
    return f"private/{user_id()}/{bill_id(priced)}.pdf"


def bad_results(message, priced={}, file=None, file_name=None, upload_id=None):
    def user_message(argument):
        switcher = {
            "embedded": """
                    Your are supplied on an embedded network. 
                    Unfortunately you can not choose your supplier. 
                    We are sorry we can not be useful to you.
                  """,
            "no_parsing": """
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
        user_ = {"user_name": "anonymous_name", "user_email": "anonymous_email"}
        send_ses_bill(file, user_, message)

    return jsonify(
        {
            'upload_id': upload_id,
            'bests': [],
            'evaluated': -1,
            'bill': priced,
            "message": message
        }), 200


def scanned_priced(pdf_file):
    ## s3_resource.Bucket(SWITCH_MARKINTELL_BUCKET).upload_file(Filename=id, Key=key_file)
    try:
        url_miswitch = "https://switch.markintell.com.au/api/pdf/scanned-bill"
        r = requests.post(url_miswitch, files={"pdf": pdf_file})
        if r.status_code == 200:
            parsed = json.loads(r.content)
            return True, parsed
    except Exception as ex:
        print(ex)
        bad_message = "Sorry we could not automatically read your bill.\n Can you please make sure you have an original PDF and then try again."
        return False, f"This is a scanned bill.\n{bad_message}"



def _parse_upload(local_file, file_name, upload_id):
    Extractor.process_pdf(local_file)
    bp = BillParser(
        xml_=Extractor.xml_,
        xml_data_=Extractor.xml_data,
        txt_=Extractor.txt_,
        file_name=file_name)
    try:
        bp.parse_bill()
    except Exception as ex:
        print(ex)
        return False, bad_results("no_parsing", file=local_file, file_name=file_name, upload_id=upload_id)
    if not bp.parser or not bp.parser.json:
        return False, bad_results("no_parsing", file=local_file, file_name=file_name, upload_id=upload_id)
    parsed = bp.parser.json
    if parsed.get("retailer"):
        if parsed["retailer"] in ["winenergy", "ocenergy", "embeddedorigin"]:
            return False, bad_results("embedded", upload_id=upload_id)
    return True, parsed

def _running_avg(parsed):
    history = get_history(BILLS_BUCKET, parsed["users_nmi"])
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
    return Bill(dict(parsed))()

def _store_data(priced, request, res, nb_offers, ranking, upload_id):
    ip = request.remote_addr
    provider = request.form.get("provider")
    email = request.form.get("email")

    if email:
        user_email = email
        user_name = email
    else:
        user_email = "anonymous_email"
        user_name = "anonymous_name"

    key_file = bill_file_name(priced)
    customer = user_id()
    try:
        populate_bill_users(priced, provider, customer, ip, user_email, user_name)
    except Exception as ex:
        print("CANNOT STORE USER PARAMETERS FROM BILL")
        print(ex)
    populate_bests_offers(res, priced, nb_offers, ranking, key_file=key_file, customer_id=customer)
    copy_object(BILLS_BUCKET, f"upload/{upload_id}.pdf",BILLS_BUCKET, key_file)

def _process_upload_miswitch(request, local_file, file_name):
    email = request.form.get("email")
    if email:
        user_email = email
        user_name = email
    else:
        user_email = "anonymous_email"
        user_name = "anonymous_name"

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

def _create_best_result(res, upload_id, nb_offers, nb_retailers, priced, ranking):
    message = "saving" if len(res) else "no saving"
    res = annomyze_offers(res)
    result = {
        "upload_id": upload_id,
        "evaluated": nb_offers,
        "ranking": ranking,
        "nb_retailers": nb_retailers,
        "bests": res,
        "bill": priced,
        "message": message}
    result = json.dumps(result, indent=4)
    return result

def _get_bests(upload_id, priced, local_file, file_name, is_business):
    try:
        res, nb_offers, nb_retailers, ranking = get_bests(priced, "", n=-1, is_business=is_business)
        _store_data(priced, request, res, nb_offers, ranking,upload_id)
        return _create_best_result(res, upload_id, nb_offers, nb_retailers, priced, ranking)
    except Exception as ex:
        print(ex)
        return bad_results("bad_best_offers", file=None, file_name=file_name, upload_id=upload_id)



@app.route("/get-upload-id", methods=["GET"])
def upload_id():
    result = {"upload_id": f"bill-{uuid.uuid1()}"}
    result = json.dumps(result)
    return result, 200


@app.route("/upload-file", methods=["POST"])
def upload_file():
    upload_id = request.form.get("upload_id")
    file_obj = request.files.get("pdf")
    file_name = file_obj.filename
    pdf_data = file_obj.read()
    local_file = f"/tmp/{upload_id}.pdf"
    with open(local_file, "wb") as out:
        out.write(pdf_data)
    key_file = f"upload/{upload_id}.pdf"
    s3_resource.Bucket(BILLS_BUCKET).upload_file(Filename=local_file, Key=key_file)
    is_bill, message = Extractor.check_bill(local_file)
    if not is_bill:
        if "scanned" in message:
            res, parsed = scanned_priced(pdf_data)
            if not res:
                return bad_results(parsed, file=local_file, file_name=file_name, upload_id=upload_id)
        else:
            return bad_results(message, file=local_file, file_name=file_name, upload_id=upload_id)
    result = {"upload_id": upload_id,
              "message": "success"}
    result = json.dumps(result)
    return result, 200


@app.route("/bests-upload", methods=["POST"])
def bests_upload():
    upload_id = request.form.get("upload_id")
    local_file = f"/tmp/{upload_id}.pdf"
    key_file = f"upload/{upload_id}.pdf"
    s3_resource.Bucket(BILLS_BUCKET).download_file(Filename=local_file, Key=key_file)
    is_business = request.form.get("is_business")
    is_business = True if is_business == "yes" else False
    file_name = f"/{upload_id}.pdf"
    status, parsed = _parse_upload(local_file, file_name, upload_id)
    if not status: return parsed
    priced = _running_avg(parsed)
    result = _get_bests(upload_id, priced, local_file, file_name, is_business)
    _process_upload_miswitch(request, local_file, file_name)
    return result, 200


@app.route("/bests", methods=["POST"])
def bests():
    ip = request.remote_addr
    upload_id = request.form.get("upload_id")

    file_obj = request.files.get("pdf")
    pdf_data = file_obj.read()
    is_business = request.form.get("is_business")
    is_business = True if is_business == "yes" else False
    provider = request.form.get("provider")
    email = request.form.get("email")
    if email:
        user_email = email
        user_name = email
    else:
        user_email = "anonymous_email"
        user_name = "anonymous_name"

    file_name = file_obj.filename
    id = f"/tmp/{uuid.uuid1()}.pdf"
    print(f"TRYING TO PARSE {file_name} copied in {id}")
    with NamedTemporaryFile("wb", suffix=".pdf", delete=False) as out:
        out.write(pdf_data)
        copyfile(out.name, id)
        is_bill, message = Extractor.check_bill(out.name)

        if not is_bill:
            if "scanned" in message:
                res, parsed = scanned_priced(pdf_data)
                if not res:
                    return bad_results(parsed, file=id, file_name=file_name, upload_id=upload_id)
            else:
                return bad_results(message, file=id, file_name=file_name, upload_id=upload_id)
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
                return bad_results("no_parsing", file=id, file_name=file_name, upload_id=upload_id)
            if not bp.parser or not bp.parser.json:
                return bad_results("no_parsing", file=id, file_name=file_name, upload_id=upload_id)
            parsed = bp.parser.json

        priced = Bill(dict(parsed))()
        if priced.get("retailer"):
            if priced["retailer"] in ["winenergy", "ocenergy", "embeddedorigin"]:
                return bad_results("embedded", upload_id=upload_id)

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
            return bad_results("bad_best_offers", file=id, file_name=file_name, upload_id=upload_id)

        key_file = bill_file_name(priced)
        customer = user_id()
        try:
            populate_bill_users(priced, provider, customer, ip, user_email, user_name)
        except Exception as ex:
            print("CANNOT STORE USER PARAMETERS FROM BILL")
            print(ex)
        populate_bests_offers(res, priced, nb_offers, ranking, key_file=key_file, customer_id=customer)
        s3_resource.Bucket(BILLS_BUCKET).upload_file(Filename=id, Key=key_file)
        ## s3_resource.Bucket(SWITCH_MARKINTELL_BUCKET).upload_file(Filename=id, Key=key_file)
        try:
            url_miswitch = "https://switch.markintell.com.au/api/pdf/pdf-to-json"
            r = requests.post(url_miswitch, files={'pdf': pdf_data},
                              data={"source": SOURCE_BILL,
                                    "file_name": "_".join(key_file.split("/")[1:]),
                                    "user_name": user_name,
                                    "user_email": user_email
                                    })
            print("reponse miswitch", r)
        except Exception as ex:
            print(ex)

        os.remove(id)
        if not len(res):
            result = {
                "upload_id": upload_id,
                "evaluated": nb_offers,
                "ranking": ranking,
                "nb_retailers": nb_retailers,
                "bests": res,
                "bill": priced,
                "message": "no saving"}
            result = json.dumps(result, indent=4)
            return result, 200
        res = annomyze_offers(res)
        result = {
            "upload_id": upload_id,
            "evaluated": nb_offers,
            "ranking": ranking,
            "nb_retailers": nb_retailers,
            "bests": res,
            "bill": priced,
            "message": "saving"}
        result = json.dumps(result, indent=4)
        return result, 200


@app.route("/confirmSignup", methods=["GET"])
def confirmSignUp():
    user_name = request.args.get('username')
    client_id = request.args.get('clientId')
    confirmation_code = request.args.get('code')
    email = request.args.get('email')
    cognito = boto3.client('cognito-idp')
    try:
        cognito.confirm_sign_up(
            ClientId=client_id,
            Username=user_name,
            ConfirmationCode=confirmation_code
        )
        result = {"confirmed": True}
    except:
        result = {"confirmed": False}

    return result, 200
