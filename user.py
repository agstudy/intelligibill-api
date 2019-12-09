import boto3
from botocore.exceptions import ClientError
import json

# if __name__=="__main__":
#     cognito = boto3.client('cognito-idp')
#
#     response = cognito.list_users(
#         UserPoolId='ap-southeast-2_IG69RgQQJ',
#         Filter='cognito:user_status="UNCONFIRMED"'
#     )
#     for k,v in response.items():
#         from pprint import pprint
#         for x in v :
#             if "Attributes" in x:
#                 print(x["Attributes"][2]["Value"],x["Username"])
#                 cognito.admin_confirm_sign_up(
#                     UserPoolId='ap-southeast-2_IG69RgQQJ',
#                     Username=x["Username"])


def _update_upload(upload_id, customer_id, bill_id_to_date, message):
    key = {"upload_id": upload_id}
    upload_table.update_item(
        Key=key,
        UpdateExpression="set customer_id=:customer_id,bill_id_to_date=:bill_id_to_date,message=:message",
        ExpressionAttributeValues= {
            ':customer_id': customer_id,
            ':bill_id_to_date': bill_id_to_date,
            ':message': message
        }, ReturnValues="UPDATED_NEW"
    )

class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        from decimal import Decimal
        if isinstance(o, Decimal):
            if o % 1 > 0:
                return float(o)
            else:
                return int(o)
        return super(DecimalEncoder, self).default(o)


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


def get_bests(customer_id, bill_id,upload_id):
    dynamodb = boto3.resource('dynamodb')
    best_offers_table = dynamodb.Table('bests_offers_free')

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
            from pprint import pprint
            r = x["tracking"]
            result = _create_best_result(
                x["bests"],
                upload_id,
                r["evaluated"],
                x.get("nb_retailers"),
                x["priced"],
                r["ranking"]
            )
            return result, 200
    except ClientError as e:
        print(e.response['Error']['Message'])
        raise e






if __name__ =='__main__':
    res, val = get_bests("61024432937-mountain", "61024432937_2019-06-22","bill-e14d27e4-1a96-11ea-8c58-06b91a971d46")
    print(res)