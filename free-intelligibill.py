from flask import Flask
from flask import request
import json
from flask_cors import CORS
import boto3
from flask_request_id_header.middleware import RequestID
from engine import manage_bill_upload, get_upload_bests, retrive_bests_by_id
app = Flask(__name__)
app.config['REQUEST_ID_UNIQUE_VALUE_PREFIX'] = 'open-'
s3_resource = boto3.resource('s3')

RequestID(app)

CORS(app)

@app.route("/upload-file", methods=["POST"])
def upload_file():
    file_obj = request.files.get("pdf")
    result, statut = manage_bill_upload(file_obj)
    return result, statut

@app.route("/search-upload-bests", methods=["POST"])
def search_upload_bests():
    upload_id = request.form.get("upload_id")
    result, statut = get_upload_bests(upload_id)
    return result, statut

@app.route("/retrieve-upload-bests", methods=["POST"])
def retrieve_upload_bests():
    upload_id = request.form.get("upload_id")
    result, statut = retrive_bests_by_id(upload_id)
    return result, statut

@app.route("/bests", methods=["POST"])
def bests():
    file_obj = request.files.get("pdf")
    result ,statut = manage_bill_upload(file_obj)
    print("result is :" , result)
    print(statut)
    if statut==200:
        result = json.loads(result)
        result ,statut = get_upload_bests(result["upload_id"])
    return result, statut



