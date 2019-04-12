
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
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from shutil import copyfile
import uuid
import io
import os
import requests
import stripe
from urllib import parse

from send_bill import send_ses_bill, send_feedback

from flask_cors import CORS
from datetime import datetime

stripe.api_key = "sk_test_D5dWWe8ArtNLsJJgLzIqX8Ss"

app = Flask(__name__)
CORS(app)
s3_resource = boto3.resource('s3')
dynamodb = boto3.resource('dynamodb')
tracker_table = dynamodb.Table('bests_offers')
users_table = dynamodb.Table('ib_users')
cognito = boto3.client('cognito-idp')

BILLS_BUCKET = "myswitch-bills-bucket"
SWITCH_MARKINTELL_BUCKET = "switch-markintell"
BAD_BILLS_BUCKET = "ib-bad-bills"


def user_id():
    return request.headers.get('user_id')

def coginto_user():

    sub = user_id()
    response = cognito.list_users(
        UserPoolId='ap-southeast-2_IG69RgQQJ',
        AttributesToGet=[
            'email',
        ],
        Filter= f'sub="{sub}"'
    )
    user_ = response["Users"][0]
    user_name = user_["Username"]
    user_email = ""
    for x in user_["Attributes"]:
        if x["Name"] =="email":
            user_email = x["Value"]
    print(f"user name is {user_name} and user email is {user_email}")
    return {"user_name":user_name,
            "user_email":user_email}

def bill_id(priced):
    return (f"""{priced["users_nmi"]}_{priced["to_date"].replace("/","-")}""")

def bill_file_name(priced):
    return f"private/{user_id()}/{bill_id(priced)}.pdf"

def populate_tracking(bests, priced, nb_offers,ranking, key_file):
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
    if customer_id:
        tracker_table.put_item(Item=item)

def populate_users(bill,  payment, customer_id, charge_id):

    user_ = coginto_user()
    creation_date = datetime.today().strftime("%Y-%m-%d-%H-%M-%S")

    item = {
      'nmi' : bill['users_nmi'],
      'address' : bill['address'],
      'name': bill['name'],
      'region': bill['region'],
      'user_name': user_['user_name'],
      'user_email': user_['user_email'],
      'creation_date': creation_date,
      'payment': payment,
      'customer_id' : customer_id,
      'charge_id' : charge_id
    }
    users_table.put_item(Item=item)

def paid_customer(nmi):

    response = users_table.get_item(Key= {'nmi':nmi})
    if 'Item' in response:
        charge_id = response['Item']["charge_id"]
        charge = stripe.Charge.retrieve(charge_id)
        return charge["paid"]



def update_tracking(bests, priced, nb_offers, ranking):
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
    tracker_table.update_item(
        Key=key,
        UpdateExpression="set priced=:priced,tracking=:tracking,best=:bests,spot_date=:spot_date",
        ExpressionAttributeValues={':priced': priced,
                                   ':bests': bests,
                                   ':tracking': tracking,
                                   ':spot_date': spot_date},
        ReturnValues="UPDATED_NEW")

def bad_results(message, priced={}, file=None, file_name=None ):

    if file:
        s3_resource.Bucket(BAD_BILLS_BUCKET).upload_file(Filename=file, Key=file_name)
        user_ = coginto_user()
        send_ses_bill(file,user_)

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
    file_bytes = open(local_file,'rb').read()
    url_miswitch = "https://switch.markintell.com.au/api/pdf/pdf-to-json"
    r = requests.post(url_miswitch , files={'pdf':file_bytes}, data = {"source":"IB"})
    print("reponse miswitch", r )

def annomyze_offers(priced, offers):
    nmi = priced["users_nmi"]
    if not paid_customer(nmi):
        for i,x in enumerate(offers):
            if x["saving"] > 100:
                o = x["origin_offer"]
                tariff = o ["tariff"]
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
                if "eligibility" in o: del o["eligibility"]
                if "eligibility" in tariff: del tariff["eligibility"]

    return offers
    pass

@app.route('/bill/page', methods=['GET'])
def bill_source():
    bill_url = request.args.get('bill_url')
    page = request.args.get('page')
    key = f"private/{user_id()}/{bill_url}"
    key_image = f"""{key.replace(".pdf","")}-page-{page}.png"""
    file_name = f"/tmp/{uuid.uuid1()}.png"
    print(f"key_image is {key_image}")
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
    pdf_data = file_obj.read()
    is_business = request.form.get("is_business")
    file_name = file_obj.filename
    print("trying to parse ...", file_name)
    id = f"/tmp/{uuid.uuid1()}.pdf"
    with NamedTemporaryFile("wb", suffix=".pdf", delete=False) as out:
        out.write(pdf_data)
        copyfile(out.name, id)
        is_bill, message = Extractor.check_bill(out.name)
        if not is_bill:
            return bad_results(message,file=id, file_name=file_name)
        Extractor.process_pdf(out.name)
        bp = BillParser(
            xml_=Extractor.xml_,
            xml_data_=Extractor.xml_data,
            txt_=Extractor.txt_,
            file_name=file_name)
        bp.parse_bill()
        if not bp.parser or not bp.parser.json:
            return bad_results("no parsing",file=id, file_name=file_name)
        parsed = bp.parser.json
        priced: dict = Bill(dict(parsed))()
        res, nb_offers, nb_retailers, ranking = get_bests(priced, "", n=-1, is_business=is_business)
        key_file = bill_file_name(priced)
        populate_tracking(res, priced, nb_offers, ranking, key_file=key_file)
        s3_resource.Bucket(BILLS_BUCKET).upload_file(Filename=id, Key=key_file)
        s3_resource.Bucket(SWITCH_MARKINTELL_BUCKET).upload_file(Filename=id, Key=key_file)
        os.remove(id)
        if not len(res):
            return bad_results("no saving")
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

@app.route("/bests/single", methods=["POST"])
def bests_single():
    params = request.get_json()
    priced = params.get("priced")
    retailer = params.get("retailer")
    print("trying to get all other retailer best offers ...")
    res, nb_offers, nb_retailers, ranking = get_bests(priced, "", n=-1, unique=False, single_retailer=retailer)
    if not len(res):
        return bad_results("no saving", priced)
    res = annomyze_offers(priced, res)

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
    update_tracking(res, priced, nb_offers, ranking)
    if not len(res):
        return bad_results("no saving", priced)

    res = annomyze_offers(priced, res)
    return jsonify(
        {"evaluated": nb_offers,
         "ranking"  : ranking,
         "nb_retailers": nb_retailers,
         "bests": res,
         "bill": priced,
         "message": "saving"}), 200

@app.route("/tracker", methods=["GET"])
def tracker():
    nmi = request.args.get('nmi')
    customer_id = user_id()
    try:
        response = tracker_table.query(
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
    print(f"customer_id is {customer_id} and to_date is {to_date} and bill_id is {bill_id}")
    try:
        response = tracker_table.query(
            KeyConditionExpression='customer_id=:id and bill_id_to_date=:bill_id',
            ExpressionAttributeValues=
            {':id': customer_id,
             ':bill_id': bill_id
             }
        )
        items = response['Items']
        print(items)
        if items:
            x = items[0]
            bests = annomyze_offers(x["priced"], x["bests"])
            result = {"bests": bests, "bill": x["priced"]}
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
        response = tracker_table.scan(
            FilterExpression='#state=:region or begins_with(bill_id_to_date , :nmi)',
            ExpressionAttributeValues=
            {':region': region,
             ':nmi': nmi
             },
             ExpressionAttributeNames={ "#state": "priced.region" },
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
    bill = params.get("bill")
    user_= coginto_user()
    customer =stripe.Customer.create(
        email= user_["user_email"],
        source = token
    )
    stripe_cus = customer.id
    charge = stripe.Charge.create(
        customer = stripe_cus,
        amount = 3000,
        currency = 'aud',
        description = f'intelligibill annual fee for NMI: {nmi}',
        receipt_email = user_["user_email"],
        metadata = {"nmi" : nmi}
    )

    payment = json.loads(json.dumps(charge), parse_float=decimal.Decimal)
    populate_users(bill, payment, stripe_cus, charge.id)
    return jsonify(charge) , 200

@app.route('/payment/is_paid', methods=['GET'])
def is_paid():
    nmi = request.args.get('nmi')
    is_paid = paid_customer(nmi)
    if not is_paid: is_paid = False
    return jsonify({
        "amount" : 30,
        "threshold" : 100,
        "is_paid": is_paid

    }), 200


@app.route("/feedback", methods=["POST"])
def feedback():
    comment = request.form.get("comment")
    file_obj = request.files.get("pdf_file")
    user_= coginto_user()
    if not file_obj:
        send_feedback( message=comment,user_= user_, bill_file=None)
        result = {"feedback": True}
        result = json.dumps(result, indent=4)
        return result, 200


    pdf_data = file_obj.read()
    id_file = f"/tmp/{uuid.uuid1()}.pdf"
    with NamedTemporaryFile("wb", suffix=".pdf", delete=False) as out:
        out.write(pdf_data)
        copyfile(out.name, id_file)
        send_feedback(id_file, comment,user_)
        result = {"feedback": True}
        result = json.dumps(result, indent=4)
        return result, 200

@app.route("/current_nmi", methods=["GET"])
def current_nmi():
    customer_id = user_id()
    try:
        response = tracker_table.query(
            KeyConditionExpression=Key('customer_id').eq(customer_id),
            ProjectionExpression="priced.users_nmi, priced.address, spot_date"
        )
        items = response['Items']
        spot_date_MAX=0
        current = None
        for x in items:
            spot_date = datetime.datetime.strptime(x["spot_date"], '%Y-%m-%d-%H-%M-%S')
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
        response = tracker_table.query(
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


# We only need this for local development.
if __name__ == '__main__':

    ## import yaml

    json_data = open('zappa_settings.yaml')
    ## env_vars = yaml.load(json_data)['api']['environment_variables']
    ## for key, val in env_vars.items():
    ##    os.environ[key] = val

    app.run(port=2003, load_dotenv=True)
