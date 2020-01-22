from flask import  request
from extractor import Extractor
from pdf_parse.parser import BillParser
from bill_pricing import Bill
from best_offer import get_bests
from datetime import datetime
from smart_meter import get_history, RunAvg
from shared import  bill_id, populate_bill_users, populate_bests_offers, copy_object, \
    _create_best_result, bad_results,  bill_file_name, user_id

from cts import SOURCE_BILL, BILLS_BUCKET, best_offers_table, upload_table
import boto3, uuid, hashlib, json, requests
s3_resource = boto3.resource('s3')

def is_connected():
   return request.headers.get('user_id') is not None


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
    history = get_history(BILLS_BUCKET, parsed["users_nmi"], parsed["to_date"])
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

def _store_data(priced, request, res, nb_offers, ranking, upload_id,nb_retailers):
    ip = request.remote_addr
    provider = request.form.get("provider")
    email = request.form.get("email")
    customer = request.form.get("customer")
    if not customer :
        customer = request.headers.get('user_id')
    if not customer:
        customer = user_id(priced, email)

    if email:
        user_email = email
        user_name = email
    else:
        user_email = "anonymous_email"
        user_name = "anonymous_name"
    key_file = bill_file_name(priced, customer)
    try:
        populate_bill_users(priced, provider, customer, ip, user_email, user_name)
    except Exception as ex:
        print("CANNOT STORE USER PARAMETERS FROM BILL")
        print(ex)
    populate_bests_offers(res, priced, nb_offers, ranking, key_file=key_file, customer_id=customer,nb_retailers=nb_retailers)
    message = "saving" if len(res) else "no_saving"
    _update_upload(
        upload_id = upload_id,
        customer_id= customer,
        bill_id_to_date= bill_id(priced),
        message = message
    )
    copy_object(BILLS_BUCKET, f"upload/{upload_id}.pdf", BILLS_BUCKET, key_file)

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

def _get_bests(upload_id, priced, file_name, is_business):
    try:
        res, nb_offers, nb_retailers, ranking = get_bests(priced, "", n=-1, is_business=is_business)
        _store_data(priced, request, res, nb_offers, ranking, upload_id, nb_retailers)
        return _create_best_result(res, upload_id, nb_offers, nb_retailers, priced, ranking)
    except Exception as ex:
        print(ex)
        return bad_results("bad_best_offers", file=None, file_name=file_name, upload_id=upload_id)

def _store_upload(upload_id, file_name, checksum, message, provider, src=None):
    creation_date = datetime.today().strftime("%Y-%m-%d-%H-%M-%S")
    if not src: src = "anonymous"

    item = {
        "upload_id": upload_id,
        "creation_date": creation_date,
        "file_name": file_name,
        "message": message,
        "provider": provider,
        "checksum": checksum,
        "from": src
    }
    customer = request.form.get("customer")
    if customer:
        item.update({"customer":customer})
    upload_table.put_item(Item=item)

def _update_upload(upload_id, customer_id, bill_id_to_date, message):
    key = {"upload_id": upload_id}
    upload_table.update_item(
        Key=key,
        UpdateExpression="set customer_id=:customer_id,bill_id_to_date=:bill_id_to_date,message=:message",
        ExpressionAttributeValues= {
            ':customer_id': customer_id,
            ':bill_id_to_date': bill_id_to_date,
            ':message': message
        }, ReturnValues="UPDATED_NEW"
    )

def manage_bill_upload(file_obj):
    upload_id = f"bill-{uuid.uuid1()}"
    file_name = file_obj.filename
    pdf_data = file_obj.read()
    checksum = hashlib.md5(pdf_data).hexdigest()
    local_file = f"/tmp/{upload_id}.pdf"
    with open(local_file, "wb") as out:
        out.write(pdf_data)
    key_file = f"upload/{upload_id}.pdf"
    s3_resource.Bucket(BILLS_BUCKET).upload_file(Filename=local_file, Key=key_file)
    is_bill, message = Extractor.check_bill(local_file)
    if is_bill: message = "success"
    provider = request.remote_addr
    _store_upload(upload_id, file_name, checksum, message, provider=provider)
    if not is_bill:
        if "scanned" in message:
            res, parsed = scanned_priced(pdf_data)
            if not res:
                return bad_results(parsed, file=local_file, file_name=file_name, upload_id=upload_id)
        else:
            return bad_results(message, file=local_file, file_name=file_name, upload_id=upload_id)
    result = {"upload_id": upload_id, "message": "success"}
    result = json.dumps(result)
    return result, 200

def get_upload_bests(upload_id):
    local_file = f"/tmp/{upload_id}.pdf"
    key_file = f"upload/{upload_id}.pdf"
    s3_resource.Bucket(BILLS_BUCKET).download_file(Filename=local_file, Key=key_file)
    is_business = request.form.get("is_business")
    is_business = True if is_business == "yes" else False
    file_name = f"/{upload_id}.pdf"
    status, parsed = _parse_upload(local_file, file_name, upload_id)
    if not status: return parsed
    priced = _running_avg(parsed)
    result = _get_bests(upload_id, priced, file_name, is_business)
    _process_upload_miswitch(request, local_file, file_name)
    return result, 200

def retrive_bests_by_id(upload_id):

    try:
        response = upload_table.query(
            KeyConditionExpression='upload_id=:id',
            ExpressionAttributeValues= {':id': upload_id}
        )
        items = response['Items']
        if items:
            x = items[0]
            customer_id = x["customer_id"]
            bill_id = x["bill_id_to_date"]
            response = best_offers_table.query(
                KeyConditionExpression='customer_id=:id and bill_id_to_date=:bill_id',
                ExpressionAttributeValues= {':id': customer_id,':bill_id': bill_id})
            items = response['Items']
            if items:
                x = items[0]
                r = x["tracking"]
                result = _create_best_result(x["bests"],upload_id,r["evaluated"],
                                             x.get("nb_retailers"),x["priced"],r["ranking"])
                return result, 200
    except Exception as e:
        print(e)
        raise e