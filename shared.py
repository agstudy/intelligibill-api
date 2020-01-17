from datetime import datetime
import json
import boto3
import os
import decimal
from botocore.exceptions import ClientError
import json
import re
from boto3.dynamodb.conditions import Key

class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, decimal.Decimal):
            if o % 1 > 0:
                return float(o)
            else:
                return int(o)
        return super(DecimalEncoder, self).default(o)


dynamodb = boto3.resource('dynamodb')

local = True
if local :
    best_offers_table = dynamodb.Table('bests_offers_prod')
    users_bill_table = dynamodb.Table('bill_users_prod')
else:
    best_offers_table = dynamodb.Table(os.environ.get('bests_offers_table'))
    users_bill_table = dynamodb.Table(os.environ.get('users_bill_table'))

# best_offers_table = dynamodb.Table(os.environ.get('bests_offers_table'))
# users_bill_table = dynamodb.Table(os.environ.get('users_bill_table'))


class BestDTO:

    green = []
    region: str
    offer_date: str
    url: str
    distributor: str
    saving: float
    offer_total_bill: float
    offer_id: str
    retailer: str
    tariff_type: str
    exit_fee: str
    origin_offer: {}
    frequency: float
    frequency_green: float

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

def populate_bests_offers(bests, priced, nb_offers, ranking, key_file,customer_id,nb_retailers=0):
    spot_date = datetime.today().strftime("%Y-%m-%d-%H-%M-%S")
    print("best offer is is: ", customer_id)
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

def retrive_bests_by_id(customer_id, bill_id, upload_id):
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
            r = x["tracking"]
            result = _create_best_result(x["bests"],upload_id,r["evaluated"],
                                         x.get("nb_retailers"),x["priced"],r["ranking"])
            return result, 200
    except ClientError as e:
        print(e.response['Error']['Message'])
        raise e

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
        ]
    )
    exists, sub, force_change = _user_exists(user_email)
    if exists:
        return sub, force_change


if __name__=='__main__':
    customer_id = "1d758e80-5d71-45e2-bd9e-4a03b2c33687"
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
    from pprint import pprint
    pprint(result)