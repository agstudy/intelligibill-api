from cts import best_offers_table
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from datetime import datetime

def get_current_nmi(customer_id):
    ## customer_id = user_id()
    response = best_offers_table.query(
        KeyConditionExpression=Key('customer_id').eq(customer_id),
        ProjectionExpression="priced.users_nmi, priced.address, spot_date"
    )
    items = response['Items']
    spot_date_MAX = datetime.strptime("2000-01-01-00-00-00", '%Y-%m-%d-%H-%M-%S')
    current = None
    for x in items:
        spot_date = datetime.strptime(x["spot_date"], '%Y-%m-%d-%H-%M-%S')
        if spot_date > spot_date_MAX:
            spot_date_MAX = spot_date
            current = x
    return current["priced"]["users_nmi"]

def current_nmi(customer_id):
    try:
        response = best_offers_table.query(
            KeyConditionExpression=Key('customer_id').eq(customer_id),
            ProjectionExpression="priced.users_nmi, priced.address, spot_date"
        )
        items = response['Items']
        spot_date_MAX = datetime.strptime("2000-01-01-00-00-00", '%Y-%m-%d-%H-%M-%S')
        current = None
        for x in items:
            spot_date = datetime.strptime(x["spot_date"], '%Y-%m-%d-%H-%M-%S')
            if spot_date > spot_date_MAX:
                spot_date_MAX = spot_date
                current = x
        return current
    except ClientError as e:
        print(e.response['Error']['Message'])
        raise e

def nmis(customer_id):
    try:
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

        return result
    except ClientError as e:
        print(e.response['Error']['Message'])
        raise e
