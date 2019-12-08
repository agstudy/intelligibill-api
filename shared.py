from datetime import datetime
import json
import boto3
import os
import decimal

dynamodb = boto3.resource('dynamodb')
best_offers_table = dynamodb.Table(os.environ.get('bests_offers_table'))
users_bill_table = dynamodb.Table(os.environ.get('users_bill_table'))



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

def populate_bests_offers(bests, priced, nb_offers, ranking, key_file,customer_id):
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
        'tracking': tracking
    }
    item = json.loads(json.dumps(item), parse_float=decimal.Decimal)
    print(item)
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


