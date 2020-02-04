from flask import Flask, send_file
from flask import jsonify, request
import boto3
from tempfile import NamedTemporaryFile
from extractor import Extractor
from bill_pricing import Bill
from best_offer import get_bests
import decimal
import json
from byb_payment.account import current_nmi, nmis
from byb_payment.payment import charge_client, coupon_client, invoice_client, is_paid, paid_customer_info
from botocore.exceptions import ClientError
from shutil import copyfile
import uuid
import io
import os
import stripe
from shared import annomyze_offers, bill_id, get_stripe_key, coginto_user
from send_bill import send_feedback
from flask_cors import CORS
from datetime import datetime
from engine import manage_bill_upload, get_upload_bests, best_single

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
COGNITO_POOL_ID = "ap-southeast-2_IG69RgQQJ"


def user_id():
    return request.headers.get('user_id')


def bill_file_name(priced):
    return f"private/{user_id()}/{bill_id(priced)}.pdf"

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


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, decimal.Decimal):
            if o % 1 > 0:
                return float(o)
            else:
                return int(o)
        return super(DecimalEncoder, self).default(o)

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
def bests_single_service():
    params = request.get_json()
    priced = params.get("priced")
    retailer = params.get("retailer")
    result, statut = best_single (priced, retailer)
    return result, statut

# @app.route("/bests/reprice", methods=["POST"])
# def reprice():
#     params = request.get_json()
#     print("trying to reprice ...")
#     parsed = params["parsed"]
#     priced: dict = Bill(dict(parsed))()
#     is_business = params["is_business"]
#     res, nb_offers, nb_retailers, ranking = get_bests(priced, "", n=-1, is_business=is_business)
#     update_bests_offers(res, priced, nb_offers, ranking)
#     if not len(res):
#         return bad_results("no saving", priced)
#     nmi = parsed["users_nmi"]
#     user_ = coginto_user()
#     if not paid_customer_info(nmi, user_["user_name"])["is_paid"]: annomyze_offers(res)
#
#     return jsonify(
#         {"evaluated": nb_offers,
#          "ranking": ranking,
#          "nb_retailers": nb_retailers,
#          "bests": res,
#          "bill": priced,
#          "message": "saving"}), 200

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
            user_ = coginto_user()
            if not paid_customer_info(nmi, user_["user_name"])["is_paid"]: annomyze_offers(x["bests"])
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


@app.route("/current_nmi", methods=["GET"])
def current_nmi_service():
    customer_id = user_id()
    current = current_nmi(customer_id)
    return jsonify(current), 200

@app.route("/nmis", methods=["GET"])
def nmis_service():
    customer_id = user_id()
    res = nmis(customer_id)
    return jsonify(res), 200

@app.route("/payment/charge", methods=["POST"])
def charge_client_service():
    params = request.get_json()
    token = params.get("stripeToken")
    nmi = params.get("nmi")
    user_ = coginto_user()
    charge = charge_client(stripe_token=token, nmi=nmi, user_name=user_["user_name"], user_email=user_["user_email"])
    return jsonify(charge), 200

@app.route("/payment/invoice", methods=["POST"])
def invoice_client_service():
    params = request.get_json()
    token = params.get("stripeToken")
    nmi = params.get("nmi")
    user_ = coginto_user()
    invoice = invoice_client(token, nmi, user_["user_email"])
    return jsonify(invoice), 200

@app.route("/payment/coupon", methods=["POST"])
def coupon_client_service():
    params = request.get_json()
    coupon = params.get("coupon")
    nmi = params.get("nmi")
    user_ = coginto_user()
    res = coupon_client( nmi= nmi, coupon= coupon,  user_name = user_["user_name"], user_email = user_["user_email"])
    return jsonify(res), 200

@app.route('/payment/is_paid', methods=['GET'])
def is_paid_service():
    user_ = coginto_user()
    user_name = user_["user_name"]
    nmi = request.args.get('nmi')
    print("is paid nmi is ", nmi)
    payment = is_paid(nmi, user_name, user_id())
    return jsonify(payment), 200
