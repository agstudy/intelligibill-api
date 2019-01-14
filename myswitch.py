from flask import Flask, Response
from flask import jsonify, request
import boto3
from tempfile import NamedTemporaryFile
from extractor import Extractor
from bill_parse.parser import BillParser
from bill_pricing import Bill
from best_offer import get_bests


app = Flask(__name__)
s3_resource = boto3.resource('s3')


@app.route('/')
def index():
    return "Hello, world!", 200


@app.route("/parse", methods=["POST"])
def parse():
    file_obj = request.files.get("pdf")
    pdf_data = file_obj.read()
    file_name = file_obj.filename
    with NamedTemporaryFile("wb",suffix=".pdf",delete=False) as out:
        out.write(pdf_data)
        s3_resource.Bucket("midummybucket").upload_file(Filename=out.name, Key=file_name)
        Extractor.process_pdf(out.name)
        bp = BillParser(xml_=Extractor.xml_,xml_data_ =Extractor.xml_data, txt_=Extractor.txt_,file_name=file_name)
        bp.parse_bill()
        parsed=  bp.parser.json
        priced: dict = Bill(dict(parsed))()
        res,nb_offers,status = get_bests(priced,"")
        bests=[ x for x in res if x["saving"]>0]

        print(f"bests are {bests}" )
        return  jsonify({"evaluated":nb_offers,
                 "bests":bests,
                 "bill":priced}),200





# We only need this for local development.
if __name__ == '__main__':
    app.run()


