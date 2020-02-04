from datetime import datetime
import boto3
import decimal
import json
from flask import jsonify
from cts import COGNITO_POOL_ID
from cts import BILLS_BUCKET, BAD_BILLS_BUCKET, best_offers_table, users_bill_table
from send_bill import send_ses_bill
from flask import request
from byb_payment.payment import paid_customer_info


s3_resource = boto3.resource('s3')
cognito = boto3.client('cognito-idp')


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, decimal.Decimal):
            if o % 1 > 0:
                return float(o)
            else:
                return int(o)
        return super(DecimalEncoder, self).default(o)


def is_disconnected():
   return request.headers.get('user_id') is None

def coginto_user():
    sub= request.headers.get('user_id')
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
    return {"user_name": user_name,
            "user_email": user_email}



def is_paid_customer(nmi):
    user_name = coginto_user()
    if not user_name:
        return {"is_paid": False}
    else:
        user_name = user_name["user_name"]
    return paid_customer_info(nmi, user_name)["is_paid"]

def annomyze_offers(offers):
    for i, x in enumerate(offers):
        if x["saving"] > 100:
            o = x["origin_offer"]
            tariff = o["tariff"]
            index = i + 1
            x["url"] = f"url_{index}"
            o["url"] = f"url_{index}"
            x["retailer"] = f"RETAILER{index}"
            o["retailer"] = f"RETAILER{index}"
            x["distributor"] = f"DISTRIBUTOR{index}"
            o["distributor"] = f"DISTRIBUTOR{index}"
            x["offer_id"] = f"OFFER_ID{index}"
            o["offer_id"] = f"OFFER_ID{index}"
            o["offer_name"] = f"OFFER_NAME_{index}"
            o["retailer_url"] = f"RETAILER_URL{index}"
            o["retailer_phone"] = f"PHONE{index}"
            x["retailer_url"] = f"RETAILER_URL{index}"
            x["retailer_phone"] = f"PHONE{index}"
            ## x["tariff_type"] = None
            x["origin_offer"] = {}

            if "eligibility" in o: del o["eligibility"]
            if "eligibility" in tariff: del tariff["eligibility"]

    return offers

def bill_id(priced):
    return (f"""{priced["users_nmi"]}_{priced["to_date"].replace("/","-")}""")

def bill_file_name(priced, user):
    return f"private/{user}/{bill_id(priced)}.pdf"

def user_id(priced, email=None):
    if email :
        name = email
    else:
        name = priced.get("name")
        if not name:
            name = "anonymous"
        else:
            name = name.lower().split()[-1]
    nmi = priced["users_nmi"].lower()
    return f"""{nmi}-{name}"""

def populate_bests_offers(bests, priced, nb_offers, ranking, key_file,customer_id,nb_retailers=0):
    spot_date = datetime.today().strftime("%Y-%m-%d-%H-%M-%S")
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
    priced = {k: v for k, v in priced.items() if v is not None}

    item = {
        'customer_id': customer_id,
        'source_bill': {'url': key_file},
        'bill_id_to_date': bill_id(priced),
        'spot_date': spot_date,
        'bests': bests,
        'priced': priced,
        'tracking': tracking,
        'nb_retailers': nb_retailers
    }
    item = json.loads(json.dumps(item), parse_float=decimal.Decimal)
    if customer_id:
        best_offers_table.put_item(Item=item)

def populate_bill_users(bill, provider, customer_id, ip,user_email, user_name):
    sub = customer_id
    creation_date = datetime.today().strftime("%Y-%m-%d-%H-%M-%S")

    item = {
        "bill_id": bill["users_nmi"],
        "creation_date": creation_date,
        "to_date": bill["to_date"],
        "user_name":user_name,
        "user_email": user_email,
        "address": bill["address"],
        "bill_user_name": bill["name"],
        "region": bill["region"],
        "sub": sub,
        "provider":provider,
        "ip":ip
    }

    users_bill_table.put_item(Item=item)

def copy_object(src_bucket_name, src_object_name,
                dest_bucket_name=None, dest_object_name=None):
    """Copy an Amazon S3 bucket object

    :param src_bucket_name: string
    :param src_object_name: string
    :param dest_bucket_name: string. Must already exist.
    :param dest_object_name: string. If dest bucket/object exists, it is
    overwritten. Default: src_object_name
    :return: True if object was copied, otherwise False
    """

    # Construct source bucket/object parameter
    copy_source = {'Bucket': src_bucket_name, 'Key': src_object_name}
    if dest_object_name is None:
        dest_object_name = src_object_name

    # Copy the object
    s3 = boto3.client('s3')
    s3.copy_object(CopySource=copy_source, Bucket=dest_bucket_name,
                       Key=dest_object_name)

def _create_best_result(res, upload_id, nb_offers, nb_retailers, priced, ranking):
    message = "saving" if len(res) else "no saving"
    nmi = priced["users_nmi"]

    if is_disconnected() or not is_paid_customer(nmi):
        res = annomyze_offers(res)

    result = {
        "upload_id": upload_id,
        "evaluated": nb_offers,
        "ranking": ranking,
        "nb_retailers": nb_retailers,
        "bests": res,
        "bill": priced,
        "message": message}
    result = json.dumps(result, indent=4, cls=DecimalEncoder)
    return result

def _user_exists(user_email):
    cognito = boto3.client('cognito-idp')
    response = cognito.list_users(
        UserPoolId='ap-southeast-2_IG69RgQQJ',
        Filter=f'username="{user_email}"'
    )

    if len(response.get("Users")):
        user = response.get("Users")
        for x in user[0]["Attributes"]:
            if x["Name"]=="sub":
                return True, x["Value"],user[0]["UserStatus"] =="FORCE_CHANGE_PASSWORD"
    else:
        return False, None, None

def byb_temporary_user(user_email):

    print("temporary user name is ", user_email)
    exists, sub, force_change = _user_exists(user_email)
    if exists:
        return sub, force_change
    cognito = boto3.client('cognito-idp')
    response = cognito.admin_create_user(
        Username= user_email,
        UserPoolId='ap-southeast-2_IG69RgQQJ',
        TemporaryPassword= "passwordchange",
        ## DesiredDeliveryMediums= ("",),
        MessageAction ="SUPPRESS",
        UserAttributes=[
            {
                'Name': 'email',
                'Value': user_email
            },
            {
                'Name': 'email_verified',
                'Value': True
            }
        ]
    )
    exists, sub, force_change = _user_exists(user_email)
    if exists:
        return sub, force_change

def bad_results(message_code, priced={}, file=None, file_name=None, upload_id=None, error = None ):
    def user_message(argument):
        switcher = {
            "embedded": """
                    Your are supplied on an embedded network. 
                    Unfortunately you can not choose your supplier. 
                    We are sorry we can not be useful to you.
                  """,
            "no_parsing": """
                    We are sorry we could not read your bill. 
                    Could you please check that it is an original PDF bill. 
                    If the problem is on our side, we will fix it and let you know 
                    if you have signed up with us
                  """,
            "bad_best_offers": """
                            We are sorry we could not parse your bill very well. 
                            Could you please check that it is an original PDF bill. 
                            If the problem is on our side, we will fix it and let you know 
                            if you have signed up with us
                          """,
            "no_single_pricing":"pricing problem"
        }
        return switcher.get(argument, message_code)

    message = user_message(message_code)
    message_email = message
    if upload_id:
        copy_object(BILLS_BUCKET, f"upload/{upload_id}.pdf", BAD_BILLS_BUCKET, f"{message_code}/{upload_id}.pdf")
        send_ses_bill(bill_file=None, user_="anonymous", user_message=message_email, upload_id=upload_id, error= error )
    elif file:
        s3_resource.Bucket(BAD_BILLS_BUCKET).upload_file(Filename=file, Key=file_name)
        send_ses_bill(bill_file=file, user_="anonymous", user_message=message)

    return jsonify(
        {
            'upload_id': upload_id,
            'bests': [],
            'evaluated': -1,
            'bill': priced,
            "message": message
        }), 200

def get_stripe_key(key):
    ssm = boto3.client('ssm', region_name='us-east-1')
    parameter = ssm.get_parameter(Name=key)
    return parameter["Parameter"]["Value"]

if __name__=='__main__':
    res = byb_temporary_user("stephanierae.patterson@gmail.com")
