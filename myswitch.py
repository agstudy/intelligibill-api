from flask import Flask
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



local = False
from flask_cors import CORS
from datetime import datetime

app = Flask(__name__)
CORS(app)
s3_resource = boto3.resource('s3')
dynamodb = boto3.resource('dynamodb')
tracking_table = dynamodb.Table('tracking_table')


def sub():
    return request.environ["API_GATEWAY_AUTHORIZER"]["claims"]["sub"]


def populate_tracking(bests,priced):

     to_day = datetime.today().strftime("%Y-%m-%d")
     customer_id = sub()

     item = {
       'customer_id': customer_id,
       'spot_date': to_day,
       'bests': bests,
       'priced':priced
     }
     item = json.loads(json.dumps(item), parse_float=decimal.Decimal)
     if customer_id:
         tracking_table.put_item(Item=item)

@app.route('/')
def index():
    print(request.headers.get('user_id'))
    return "Hello, world!", 200


def bad_results(message):
    return  jsonify(
            { 'bests': [],
              'evaluated' : -1,
              'bill': {},
              "message":message
            }), 200

@app.route("/parse", methods=["POST"])
def parse():
    file_obj = request.files.get("pdf")
    pdf_data = file_obj.read()
    file_name = file_obj.filename
    print("trying to parse ...",file_name)
    with NamedTemporaryFile("wb",suffix=".pdf",delete=False) as out:
        out.write(pdf_data)
        is_bill, message =  Extractor.check_bill(out.name)
        if not is_bill:
            return  bad_results(message)

        out.write(pdf_data)
        if not local:
            s3_resource.Bucket("midummybucket").upload_file(Filename=out.name, Key=file_name)
        Extractor.process_pdf(out.name)
        bp = BillParser(xml_=Extractor.xml_,xml_data_ =Extractor.xml_data, txt_=Extractor.txt_,file_name=file_name)
        bp.parse_bill()
        if not bp.parser:
            return bad_results("no parsing")
        parsed = bp.parser.json

        priced: dict = Bill(dict(parsed))()
        res,nb_offers,status = get_bests(priced,"",n=-1)
        bests=[ x for x in res if x["saving"]>0]
        if not local:
            populate_tracking(bests,priced)
        if not len(bests):
            return  bad_results("no saving")

        return  jsonify(
            {"evaluated":nb_offers,
             "bests":bests,
             "bill":priced,
             "message":"saving"}),200


@app.route("/history",methods=["GET"])
def history():

    id = "0186b45b-c273-4860-9bc0-70d0a70206b6"
    response = tracking_table.query(
        KeyConditionExpression=Key('customer_id').eq(id))
    return  jsonify({"items":response['Items']}), 200


@app.route("/check", methods=["POST"])
def check():
    file_obj = request.files.get("pdf")
    pdf_data = file_obj.read()
    file_name = file_obj.filename
    with NamedTemporaryFile("wb",suffix=".pdf",delete=False) as out:
        out.write(pdf_data)
        is_bill, message =  Extractor.check_bill(out.name)
        print("check ", file_name)
        print(is_bill, message)
        print(Extractor.txt_)
        return jsonify(
            {"is_bill":is_bill,
             "message":message}
        )


# We only need this for local development.
if __name__ == '__main__':
    import os


    import yaml
    import os
    json_data = open('zappa_settings.yaml')
    env_vars = yaml.load(json_data)['api']['environment_variables']
    for key, val in env_vars.items():
        os.environ[key] = val


    app.run(port =2003,load_dotenv=True)


