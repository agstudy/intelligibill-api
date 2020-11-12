import boto3, os

## tables
dynamodb = boto3.resource('dynamodb')
upload_table = dynamodb.Table(os.environ.get('upload_table'))
best_offers_table = dynamodb.Table(os.environ.get('bests_offers_table'))
users_bill_table = dynamodb.Table(os.environ.get('users_bill_table'))
users_paid_table = dynamodb.Table(os.environ.get('users_paid_table'))
offers_table = dynamodb.Table(os.environ.get("offers_table","offers"))
## buckets
BILLS_BUCKET = os.environ.get('bills-bucket')
BAD_BILLS_BUCKET = "ib-bad-bills"
SWITCH_MARKINTELL_BUCKET = os.environ.get("switch-bucket")
SOURCE_BILL = os.environ.get("source-bill")
## cts
COGNITO_POOL_ID = "ap-southeast-2_IG69RgQQJ"

# payment
AMOUNT = 3000
COUPON_TOKEN = os.environ.get("coupon")


