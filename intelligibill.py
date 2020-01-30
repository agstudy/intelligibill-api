from flask import Flask, send_file
from flask import jsonify, request
import boto3
from tempfile import NamedTemporaryFile
from extractor import Extractor
from bill_pricing import Bill
from best_offer import get_bests
import decimal
import json
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from shutil import copyfile
import uuid
import io
import os
import requests
import stripe
from urllib import parse
from shared import annomyze_offers, bill_id, get_stripe_key

from send_bill import send_ses_bill, send_feedback

from flask_cors import CORS
from datetime import datetime
from decimal import Decimal

stripe.api_key = get_stripe_key(os.environ.get("stripe.api_key"))
COUPON_TOKEN = os.environ.get("coupon")
SOURCE_BILL = os.environ.get("source-bill")

app = Flask(__name__)
CORS(app)
cognito = boto3.client('cognito-idp')
s3_resource = boto3.resource('s3')
dynamodb = boto3.resource('dynamodb')
best_offers_table = dynamodb.Table(os.environ.get('bests_offers_table'))
users_paid_table = dynamodb.Table(os.environ.get('users_paid_table'))
users_bill_table = dynamodb.Table(os.environ.get('users_bill_table'))

BILLS_BUCKET = os.environ.get('bills-bucket')
SWITCH_MARKINTELL_BUCKET = os.environ.get("switch-bucket")
BAD_BILLS_BUCKET = "ib-bad-bills"
AMOUNT = 3000
COGNITO_POOL_ID = "ap-southeast-2_IG69RgQQJ"
from engine import manage_bill_upload, get_upload_bests

def user_id():
    return request.headers.get('user_id')

def coginto_user(sub=None):
    if not sub: sub = user_id()
    response = cognito.list_users(
        UserPoolId=COGNITO_POOL_ID,
        AttributesToGet=[
            'email',
        ],
        Filter=f'sub="{sub}"'
    )
    user_ = response["Users"][0]
    user_name = user_["Username"]
    user_email = ""
    for x in user_["Attributes"]:
        if x["Name"] == "email":
            user_email = x["Value"]
    return {"user_name": user_name, "user_email": user_email}

def bill_file_name(priced):
    return f"private/{user_id()}/{bill_id(priced)}.pdf"

def populate_paid_users(nmi, payment, customer_id=None, charge_id=None, coupon=None):
    user_ = coginto_user()
    creation_date = datetime.today().strftime("%Y-%m-%d-%H-%M-%S")

    item = {
        'nmi': nmi,
        'user_name': user_['user_name'],
        'user_email': user_['user_email'],
        'creation_date': creation_date,
        'payment': payment,
    }
    if customer_id:
        item.update(
            {
                'customer_id': customer_id,
                'charge_id': charge_id
            }
        )
    if coupon:
        item.update(
            {
                'coupon': coupon,
            })
    users_paid_table.put_item(Item=item)

def paid_customer(nmi):
    response = users_paid_table.get_item(Key={'nmi': nmi})
    if 'Item' in response:
        item = response["Item"]
        user_name = coginto_user()["user_name"]
        if item["user_name"] != user_name:
           return {"is_paid": False}

        if "charge_id" in item:
            charge_id = response["Item"]["charge_id"]
            charge = stripe.Charge.retrieve(charge_id)
            return {
                "is_paid": charge["paid"],
                "receipt": charge["receipt_url"],
                "amount": charge["amount"] / 100,
                "payment_date": item["creation_date"]
            }
        elif "coupon" in item:
            payment = item["payment"]
            return {
                "is_paid": payment["paid"],
                "receipt": payment["receipt_url"],
                "amount": float(payment["amount"]),
                "payment_date": item["creation_date"]
            }
    return {"is_paid": False}

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
                  """
        }
        return switcher.get(argument, message)


    message = user_message(message)
    if file:
        s3_resource.Bucket(BAD_BILLS_BUCKET).upload_file(Filename=file, Key=file_name)
        user_ = coginto_user()
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
    user_ib = coginto_user(sub)
    file_bytes = open(local_file, 'rb').read()
    url_miswitch = "https://switch.markintell.com.au/api/pdf/pdf-to-json"
    r = requests.post(url_miswitch, files={'pdf': file_bytes},
                      data={"source": SOURCE_BILL,
                            "file_name": "_".join(key.split("/")[1:]),
                            "user_email": user_ib["user_email"],
                            "user_name": user_ib["user_name"]
                            })
    print("reponse miswitch", r)

@app.route('/bill/page', methods=['GET'])
def bill_source():
    bill_url = request.args.get('bill_url')
    page = request.args.get('page')
    key = f"private/{user_id()}/{bill_url}"
    key_image = f"""{key.replace(".pdf","")}-page-{page}.png"""
    file_name = f"/tmp/{uuid.uuid1()}.png"
    s3_resource.Bucket(BILLS_BUCKET).download_file(Filename=file_name, Key=key_image)
    with open(file_name, 'rb') as bites:
        return send_file(
            io.BytesIO(bites.read()),
            attachment_filename=key_image,
            mimetype='image/png'
        )

@app.route('/bill/pdf', methods=['GET'])
def bill_pdf():
    bill_url = request.args.get('bill_url')
    key = f"private/{user_id()}/{bill_url}"
    file_name = f"/tmp/{uuid.uuid1()}.pdf"
    s3_resource.Bucket(BILLS_BUCKET).download_file(Filename=file_name, Key=key)
    with open(file_name, 'rb') as bites:
        return send_file(
            io.BytesIO(bites.read()),
            attachment_filename=bill_url,
            mimetype='application/pdf'
        )

@app.route("/bests", methods=["POST"])
def bests():
    file_obj = request.files.get("pdf")
    result ,statut = manage_bill_upload(file_obj)
    print("result is :" , result)
    print(statut)
    if statut==200:
        result = json.loads(result)
        result ,statut = get_upload_bests(result["upload_id"], result["parsed"])
    return result, statut


@app.route("/bests/single", methods=["POST"])
def bests_single():
    params = request.get_json()
    priced = params.get("priced")
    retailer = params.get("retailer")
    print("trying to get all other retailer best offers ...")
    res, nb_offers, nb_retailers, ranking = get_bests(priced, "", n=-1, unique=False, single_retailer=retailer)
    if not len(res):
        return bad_results("no saving", priced)
    nmi = priced["users_nmi"]
    if not paid_customer(nmi)["is_paid"]: annomyze_offers(res)
    result = {"evaluated": nb_offers,
              "ranking": ranking,
              "nb_retailers": 1,
              "bests": res,
              "bill": priced,
              "message": "saving"}
    result = json.dumps(result, indent=4)
    return result, 200

@app.route("/bests/reprice", methods=["POST"])
def reprice():
    params = request.get_json()
    print("trying to reprice ...")
    parsed = params["parsed"]
    priced: dict = Bill(dict(parsed))()
    is_business = params["is_business"]
    res, nb_offers, nb_retailers, ranking = get_bests(priced, "", n=-1, is_business=is_business)
    update_bests_offers(res, priced, nb_offers, ranking)
    if not len(res):
        return bad_results("no saving", priced)
    nmi = parsed["users_nmi"]
    if not paid_customer(nmi)["is_paid"]: annomyze_offers(res)
    return jsonify(
        {"evaluated": nb_offers,
         "ranking": ranking,
         "nb_retailers": nb_retailers,
         "bests": res,
         "bill": priced,
         "message": "saving"}), 200

@app.route("/tracker", methods=["GET"])
def tracker():
    nmi = request.args.get('nmi')
    customer_id = user_id()
    try:
        response = best_offers_table.query(
            KeyConditionExpression='customer_id=:id and begins_with(bill_id_to_date , :nmi)',
            ExpressionAttributeValues=
            {':id': customer_id,
             ':nmi': nmi
             }
        )
        items = response['Items']
        result = []
        for x in items:
            bill_url = os.path.basename(x["source_bill"]["url"])
            nb_pages = len(x["source_bill"].get("images", []))
            item = {"nb_pages": nb_pages, "bill_url": bill_url}
            tracking = x.get("tracking", None)
            if tracking:
                item.update(tracking)
            result.append(item)
        result = json.dumps(result, indent=4, cls=DecimalEncoder)
        return result, 200
    except ClientError as e:
        print(e.response['Error']['Message'])
        raise e

@app.route("/tracker/detail", methods=["GET"])
def tracker_detail():
    nmi = request.args.get('nmi')
    to_date = request.args.get('to_date')
    bill_id = f"""{nmi}_{to_date.replace("/","-")}"""
    customer_id = user_id()
    try:
        response = best_offers_table.query(
            KeyConditionExpression='customer_id=:id and bill_id_to_date=:bill_id',
            ExpressionAttributeValues=
            {':id': customer_id,
             ':bill_id': bill_id
             }
        )
        items = response['Items']
        if items:
            x = items[0]
            if not paid_customer(nmi)["is_paid"]: annomyze_offers( x["bests"])
            result = {"bests": x["bests"] , "bill": x["priced"]}
            result = json.dumps(result, indent=4, cls=DecimalEncoder)
            return result, 200
    except ClientError as e:
        print(e.response['Error']['Message'])
        raise e

@app.route("/check", methods=["POST"])
def check():
    file_obj = request.files.get("pdf")
    pdf_data = file_obj.read()
    with NamedTemporaryFile("wb", suffix=".pdf", delete=False) as out:
        out.write(pdf_data)
        is_bill, message = Extractor.check_bill(out.name)
        return jsonify(
            {"is_bill": is_bill,
             "message": message}
        )

@app.route("/admin/bills", methods=["GET"])
def admin_bills():
    nmi = request.args.get('nmi')
    region = request.args.get('region')
    try:
        response = best_offers_table.scan(
            FilterExpression='#state=:region or begins_with(bill_id_to_date , :nmi)',
            ExpressionAttributeValues=
            {':region': region,
             ':nmi': nmi
             },
            ExpressionAttributeNames={"#state": "priced.region"},
        )
        items = response['Items']
        result = []
        for x in items:
            item = {"bests": x["bests"], "bill": x["priced"]}
            result.append(item)
        result = json.dumps(result, indent=4, cls=DecimalEncoder)
        return result, 200
    except ClientError as e:
        print(e.response['Error']['Message'])
        raise e

@app.route("/payment/charge", methods=["POST"])
def charge_client():
    params = request.get_json()
    token = params.get("stripeToken")

    nmi = params.get("nmi")
    user_ = coginto_user()
    customer = stripe.Customer.create(
        email=user_["user_email"],
        source=token,
        name= user_["user_name"],
        description=nmi
    )
    stripe_cus = customer.id
    charge = stripe.Charge.create(
        customer=stripe_cus,
        amount=AMOUNT,
        currency='aud',
        description=f'BeatYourBill annual fee for NMI: {nmi} [including ($2.73) gst]',
        receipt_email=user_["user_email"],
        metadata={"nmi": nmi}
    )
    print(f"/payment/charge charge is {charge}")
    payment = json.loads(json.dumps(charge), parse_float=decimal.Decimal)
    populate_paid_users(nmi, payment, stripe_cus, charge.id)
    return jsonify(charge), 200

@app.route("/payment/invoice", methods=["POST"])
def invoice_client():
    params = request.get_json()
    token = params.get("stripeToken")

    nmi = params.get("nmi")
    user_ = coginto_user()
    customer = stripe.Customer.create(
        email=user_["user_email"],
        source=token
    )
    stripe_cus = customer.id
    tax_rate = stripe.TaxRate.create(
        display_name='GST',
        jurisdiction='AUS',
        percentage=10.0
    )

    stripe.InvoiceItem.create(
        customer=stripe_cus,
        amount=AMOUNT,
        currency='aud',
        tax_rates=tax_rate,
        description=f'BeatYourBill annual fee for NMI: {nmi}'
    )
    invoice = stripe.Invoice.create(
        customer=stripe_cus,
        auto_advance=True
    )

    payment = json.loads(json.dumps(invoice), parse_float=decimal.Decimal)
    populate_paid_users(nmi, payment, stripe_cus, invoice.id)
    return jsonify(invoice), 200

@app.route("/payment/coupon", methods=["POST"])
def coupon_client():
    params = request.get_json()
    coupon = params.get("coupon")
    nmi = params.get("nmi")

    res = {
        "paid": None,
        "receipt_url": None,
        "status": None
    }
    if coupon and coupon == COUPON_TOKEN:
        res = {
            "paid": True,
            "receipt_url": "coupon",
            "status": "OK"
        }
        payment = dict(res)
        payment.update(
            {
                "coupon": coupon,
                "amount": Decimal(AMOUNT / 100)
            }
        )
        populate_paid_users(nmi, payment=payment, coupon=coupon)
    return jsonify(res), 200

@app.route('/payment/is_paid', methods=['GET'])
def is_paid():
    nmi = request.args.get('nmi')
    if not nmi:
        nmi = get_current_nmi()
    payment = paid_customer(nmi)

    payment.update({"threshold": 100})
    return jsonify(payment), 200

@app.route("/feedback", methods=["POST"])
def feedback():
    comment = request.form.get("comment")
    file_obj = request.files.get("pdf_file")
    user_ = coginto_user()
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

def get_current_nmi():
    customer_id = user_id()
    response = best_offers_table.query(
        KeyConditionExpression=Key('customer_id').eq(customer_id),
        ProjectionExpression="priced.users_nmi, priced.address, spot_date"
    )
    items = response['Items']
    spot_date_MAX = datetime.strptime("2000-01-01-00-00-00", '%Y-%m-%d-%H-%M-%S')
    current = None
    for x in items:
        spot_date = datetime.strptime(x["spot_date"], '%Y-%m-%d-%H-%M-%S')
        if spot_date > spot_date_MAX:
            spot_date_MAX = spot_date
            current = x
    print(f"current is {current}")
    return current["priced"]["users_nmi"]

@app.route("/current_nmi", methods=["GET"])
def current_nmi():
    customer_id = user_id()
    try:
        response = best_offers_table.query(
            KeyConditionExpression=Key('customer_id').eq(customer_id),
            ProjectionExpression="priced.users_nmi, priced.address, spot_date"
        )
        items = response['Items']
        spot_date_MAX = datetime.strptime("2000-01-01-00-00-00", '%Y-%m-%d-%H-%M-%S')
        current = None
        for x in items:
            spot_date = datetime.strptime(x["spot_date"], '%Y-%m-%d-%H-%M-%S')
            if spot_date > spot_date_MAX:
                spot_date_MAX = spot_date
                current = x
        return jsonify(current), 200
    except ClientError as e:
        print(e.response['Error']['Message'])
        raise e

@app.route("/nmis", methods=["GET"])
def nmis():
    customer_id = user_id()
    try:
        response = best_offers_table.query(
            KeyConditionExpression=Key('customer_id').eq(customer_id),
            ProjectionExpression="priced.users_nmi, priced.address"
        )
        items = response['Items']
        keys = set()
        result = []
        for x in items:
            if not x["priced"]["users_nmi"] in keys:
                result.append(x["priced"])
            keys.add(x["priced"]["users_nmi"])

        return jsonify(result), 200
    except ClientError as e:
        print(e.response['Error']['Message'])
        raise e

