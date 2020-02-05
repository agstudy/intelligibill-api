from cts import best_offers_table
from shared import DecimalEncoder
import json
from botocore.exceptions import ClientError


def admin_bills(nmi, region):
    try:
        response = best_offers_table.scan(
            FilterExpression='#state=:region or begins_with(bill_id_to_date , :nmi)',
            ExpressionAttributeValues=
            {':region': region,
             ':nmi': nmi
             },
            ExpressionAttributeNames={"#state": "priced.region"},
        )
        items = response['Items']
        result = []
        for x in items:
            item = {"bests": x["bests"], "bill": x["priced"]}
            result.append(item)
        result = json.dumps(result, indent=4, cls=DecimalEncoder)
        return result, 200
    except ClientError as e:
        print(e.response['Error']['Message'])
        raise e

