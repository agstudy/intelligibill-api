from flask import Flask
from flask import request
import json
from flask_cors import CORS
import boto3
from engine import manage_bill_upload, get_upload_bests, retrive_bests_by_id, \
    admin_bests, admin_bests_reprice

from byb_admin.bests import get_offer_detail
from byb_email.feedback import contact_message

app = Flask(__name__)
app.config['REQUEST_ID_UNIQUE_VALUE_PREFIX'] = 'open-'
s3_resource = boto3.resource('s3')


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
    if statut==200 and json.loads(result)["code"]=="success":
        result = json.loads(result)
        result ,statut = get_upload_bests(result["upload_id"], result["parsed"])

    return result, statut

@app.route("/contact", methods=["POST"])
def contact_service():
    message = request.form.get("message")
    email = request.form.get("email")
    result, statut = contact_message(message=message, email= email)
    return result, statut

@app.route("/admin/bests", methods=["POST"])
def byb_backend_bests():
    file_obj = request.files.get("pdf")
    result ,statut = manage_bill_upload(file_obj)
    if statut==200 and json.loads(result)["code"]=="success":
        result = json.loads(result)
        result = admin_bests(result["upload_id"])
        return result, 200
    return {} , 200

@app.route("/admin/reprice", methods=["POST"])
def byb_backend_reprice():
   params = request.get_json()
   parsed = params.get("parsed")
   is_business = params.get("is_business")
   upload_id = params.get("upload_id")

   result = admin_bests_reprice(
       parsed=parsed,
       is_business=is_business,
       upload_id=upload_id)
   return result, 200



@app.route('/admin/offers/<string:offer_id>')
def offer_details(offer_id):

    offer = get_offer_detail(offer_id)
    return offer, 200

