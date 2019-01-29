from flask import Flask, send_file
from flask import jsonify, request
import boto3
from tempfile import NamedTemporaryFile
from extractor import Extractor
from bill_parse.parser import BillParser
from bill_pricing import Bill
from best_offer import get_bests
import decimal
import json
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError
from shutil import copyfile
import uuid
import io
import os

local = False
from flask_cors import CORS
from datetime import datetime

app = Flask(__name__)
CORS(app)
s3_resource = boto3.resource('s3')
dynamodb = boto3.resource('dynamodb')
tracker_table = dynamodb.Table('tracker_table')

BILLS_BUCKET = "myswitch-bills-bucket"


def user_id():
    return request.headers.get('user_id')


def bill_id(priced):
    return (f"""{priced["to_date"].replace("/","-")}_{priced["users_nmi"]}""")


def bill_file_name(priced):
    return f"private/{user_id()}/{bill_id(priced)}.pdf"


def populate_tracking(bests, priced, nb_offers, key_file):
    spot_date = datetime.today().strftime("%Y-%m-%d-%H-%M-%S")
    customer_id = user_id()

    saving = -1;
    if len(bests):
        saving = bests[0]["saving"]
    tracking = {
        'avg_price': priced["avg"],
        'ranking': f"{len(bests)}/{nb_offers}",
        'saving': saving,
        'to_date': priced["to_date"]
    }

    item = {
        'customer_id': customer_id,
        'source_bill': {'url': key_file},
        'to_date_bill_id': bill_id(priced),
        'spot_date': spot_date,
        'bests': bests,
        'priced': priced,
        'tracking': tracking
    }
    item = json.loads(json.dumps(item), parse_float=decimal.Decimal)
    if customer_id:
        tracker_table.put_item(Item=item)


# TODO: finish this
def update_tracking(bests, priced, nb_offers):
    spot_date = datetime.today().strftime("%Y-%m-%d-%H-%M-%S")
    customer_id = user_id()

    saving = -1;
    if len(bests):
        saving = bests[0]["saving"]
    tracking = {
        'avg_price': priced["avg"],
        'ranking': f"{len(bests)}/{nb_offers}",
        'saving': saving,
        'to_date': priced["to_date"]
    }

    priced = json.loads(json.dumps(priced), parse_float=decimal.Decimal)
    bests = json.loads(json.dumps(bests), parse_float=decimal.Decimal)
    tracking = json.loads(json.dumps(tracking), parse_float=decimal.Decimal)
    key = {
        "to_date_bill_id": bill_id(priced),
        "customer_id": customer_id}
    tracker_table.update_item(
        Key=key,
        UpdateExpression="set priced=:priced,tracking=:tracking,best=:bests,spot_date=:spot_date",
        ExpressionAttributeValues={':priced': priced,
                                   ':bests': bests,
                                   ':tracking': tracking,
                                   ':spot_date': spot_date},
        ReturnValues="UPDATED_NEW")


def bad_results(message, priced={}):
    return jsonify(
        {'bests': [],
         'evaluated': -1,
         'bill': priced,
         "message": message
         }), 200


@app.route('/bill/page', methods=['GET'])
def bill_source():
    bill_url = request.args.get('bill_url')
    page = request.args.get('page')
    key = f"private/{user_id()}/{bill_url}"
    key_image = f"""{key.replace(".pdf","")}-page-{page}.jpg"""
    file_name = f"/tmp/{uuid.uuid1()}.jpg"
    print(f"key_image is {key_image}")
    s3_resource.Bucket(BILLS_BUCKET).download_file(Filename=file_name, Key=key_image)
    with open(file_name, 'rb') as bites:
        return send_file(
            io.BytesIO(bites.read()),
            attachment_filename=key_image,
            mimetype='image/jpg'
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


@app.route("/parse", methods=["POST"])
def parse():
    file_obj = request.files.get("pdf")
    pdf_data = file_obj.read()
    file_name = file_obj.filename
    print("trying to parse ...", file_name)
    id = f"/tmp/{uuid.uuid1()}.pdf"
    with NamedTemporaryFile("wb", suffix=".pdf", delete=False) as out:
        out.write(pdf_data)
        is_bill, message = Extractor.check_bill(out.name)
        if not is_bill:
            return bad_results(message)

        out.write(pdf_data)
        copyfile(out.name, id)
        Extractor.process_pdf(out.name)
        bp = BillParser(xml_=Extractor.xml_, xml_data_=Extractor.xml_data, txt_=Extractor.txt_, file_name=file_name)
        bp.parse_bill()
        if not bp.parser:
            return bad_results("no parsing")
        parsed = bp.parser.json

        priced: dict = Bill(dict(parsed))()
        res, nb_offers, nb_retailers = get_bests(priced, "", n=-1)
        bests = [x for x in res if x["saving"] > 0]

        if not local:
            key_file = bill_file_name(priced)
            populate_tracking(bests, priced, nb_offers, key_file=key_file)
            s3_resource.Bucket(BILLS_BUCKET).upload_file(Filename=id, Key=key_file)
            os.remove(id)

        if not len(bests):
            return bad_results("no saving", priced)

        return jsonify(
            {"evaluated": nb_offers,
             "nb_retailers": nb_retailers,
             "bests": bests,
             "bill": priced,
             "message": "saving"}), 200


# Helper class to convert a DynamoDB item to JSON.
class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, decimal.Decimal):
            if o % 1 > 0:
                return float(o)
            else:
                return int(o)
        return super(DecimalEncoder, self).default(o)


@app.route("/tracker", methods=["GET"])
def tracker():
    customer_id = user_id()
    try:
        response = tracker_table.query(KeyConditionExpression=Key('customer_id').eq(customer_id))
        items = response['Items']
        tracking = []
        for x in items:
            item = x.get("tracking", None)
            if item:
                if "source_bill" in x:
                    bill_url = os.path.basename(x["source_bill"]["url"])
                    nb_pages = len(x["source_bill"].get("images", []))
                    item.update({"nb_pages": nb_pages, "bill_url": bill_url})
                tracking.append(item)
        result = json.dumps(tracking, indent=4, cls=DecimalEncoder)
        return result, 200
    except ClientError as e:
        print(e.response['Error']['Message'])
        raise e


@app.route("/check", methods=["POST"])
def check():
    file_obj = request.files.get("pdf")
    pdf_data = file_obj.read()
    file_name = file_obj.filename
    with NamedTemporaryFile("wb", suffix=".pdf", delete=False) as out:
        out.write(pdf_data)
        is_bill, message = Extractor.check_bill(out.name)
        return jsonify(
            {"is_bill": is_bill,
             "message": message}
        )


@app.route("/reprice", methods=["POST"])
def reprice():

    parsed = request.get_json()
    print("trying to reprice ...")
    priced: dict = Bill(dict(parsed))()
    res, nb_offers, nb_retailers = get_bests(priced, "", n=-1)
    bests = [x for x in res if x["saving"] > 0]

    if not local:
        update_tracking(bests, priced, nb_offers)
    if not len(bests):
        return bad_results("no saving", priced)
    return jsonify(
        {"evaluated": nb_offers,
         "nb_retailers": nb_retailers,
         "bests": bests,
         "bill": priced,
         "message": "saving"}), 200


# We only need this for local development.
if __name__ == '__main__':

    import yaml

    json_data = open('zappa_settings.yaml')
    env_vars = yaml.load(json_data)['api']['environment_variables']
    for key, val in env_vars.items():
        os.environ[key] = val

    app.run(port=2003, load_dotenv=True)
