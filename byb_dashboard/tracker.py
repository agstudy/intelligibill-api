from cts import best_offers_table
from shared import DecimalEncoder, _create_best_result
from botocore.exceptions import ClientError
import json, os

def tracker_view(nmi, customer_id):

    try:
        response = best_offers_table.query(
            KeyConditionExpression='customer_id=:id and begins_with(bill_id_to_date , :nmi)',
            ExpressionAttributeValues=
            {':id': customer_id,
             ':nmi': nmi
             }
        )
        items = response['Items']
        result = []
        for x in items:
            bill_url = os.path.basename(x["source_bill"]["url"])
            nb_pages = len(x["source_bill"].get("images", []))
            item = {"nb_pages": nb_pages, "bill_url": bill_url}
            tracking = x.get("tracking", None)
            if tracking:
                item.update(tracking)
            result.append(item)
        result = json.dumps(result, indent=4, cls=DecimalEncoder)
        return result, 200
    except ClientError as e:
        print(e.response['Error']['Message'])
        raise e

def tracker_detail_view(nmi, to_date, customer_id, upload_id):

    bill_id = f"""{nmi}_{to_date.replace("/","-")}"""
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


