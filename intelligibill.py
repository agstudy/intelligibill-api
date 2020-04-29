import boto3,  json, os
from flask import Flask, jsonify, request
from flask_cors import CORS
import stripe

from shared import get_stripe_key, coginto_user
from byb_email.feedback import receive_feedback
from engine import manage_bill_upload, get_upload_bests, best_single, reprice_existing
from byb_dashboard.tracker import tracker_view, tracker_detail_view
from byb_dashboard.source_bill import send_bill_page, send_bill_pdf
from byb_payment.account import current_nmi, nmis
from byb_payment.payment import charge_client, coupon_client, invoice_client, is_paid


stripe.api_key = get_stripe_key(os.environ.get("stripe.api_key"))

app = Flask(__name__)
CORS(app)
cognito = boto3.client('cognito-idp')
s3_resource = boto3.resource('s3')
dynamodb = boto3.resource('dynamodb')

best_offers_table = dynamodb.Table(os.environ.get('bests_offers_table'))
BILLS_BUCKET = os.environ.get('bills-bucket')


def user_id():
    """
    if user is connected this function returns the user sub
    :return: returns the user sub
    """
    return request.headers.get('user_id')

@app.route('/bill/page', methods=['GET'])
def bill_page():
    bill_url = request.args.get('bill_url')
    page = request.args.get('page')
    return send_bill_page(bill_url=bill_url, page = page, user_id=user_id())

@app.route('/bill/pdf', methods=['GET'])
def bill_pdf():
    bill_url = request.args.get('bill_url')
    return send_bill_pdf(bill_url=bill_url, user_id=user_id())

@app.route("/bests", methods=["POST"])
def bests_service():
    file_obj = request.files.get("pdf")
    result ,statut = manage_bill_upload(file_obj)
    if statut==200 and json.loads(result)["code"]=="success":
        result = json.loads(result)
        result ,statut = get_upload_bests(result["upload_id"], result["parsed"])
    return result, statut

@app.route("/bests/single", methods=["POST"])
def bests_single_service():
    params = request.get_json()
    priced = params.get("priced")
    retailer = params.get("retailer")
    result, statut = best_single(priced, retailer)
    return result, statut

@app.route("/bests/reprice", methods=["POST"])
def reprice_service():
    params = request.get_json()
    parsed = params["parsed"]
    is_business = params["is_business"]
    reprice_existing(parsed=parsed, is_business=is_business)

@app.route("/tracker", methods=["GET"])
def tracker_view_service():
    nmi = request.args.get('nmi')
    customer_id = user_id()
    result, statut = tracker_view(nmi=nmi, customer_id=customer_id)
    return result, statut

@app.route("/tracker/detail", methods=["GET"])
def tracker_detail_service():
    nmi = request.args.get('nmi')
    to_date = request.args.get('to_date')
    customer_id = user_id()
    result, statut = tracker_detail_view(nmi=nmi, to_date=to_date, customer_id=customer_id, upload_id=None)
    return result, statut

@app.route("/feedback", methods=["POST"])
def feedback_service():
    comment = request.form.get("comment")
    file_obj = request.files.get("pdf_file")
    user_ = coginto_user()
    result, statut = receive_feedback(comment=comment, file_obj= file_obj, user_= user_)
    return result, statut

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
    res = coupon_client(nmi=nmi, coupon=coupon, user_name=user_["user_name"], user_email=user_["user_email"])
    return jsonify(res), 200

@app.route('/payment/is_paid', methods=['GET'])
def is_paid_service():
    user_ = coginto_user()
    user_name = user_["user_name"]
    nmi = request.args.get('nmi')
    payment = is_paid(nmi, user_name, user_id())
    return jsonify(payment), 200
