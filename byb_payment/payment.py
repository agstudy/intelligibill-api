import stripe
from cts import AMOUNT, users_paid_table, COUPON_TOKEN
import json
from datetime import datetime
import decimal
from byb_payment.account import get_current_nmi


def populate_paid_users(nmi, payment, user_name, user_email, customer_id=None, charge_id=None, coupon=None):
    ## user_ = coginto_user()
    creation_date = datetime.today().strftime("%Y-%m-%d-%H-%M-%S")

    item = {
        'nmi': nmi,
        'user_name': user_name,
        'user_email': user_email,
        'creation_date': creation_date,
        'payment': payment,
    }
    if customer_id:
        item.update(
            {
                'customer_id': customer_id,
                'charge_id': charge_id
            }
        )
    if coupon:
        item.update(
            {
                'coupon': coupon,
            })
    users_paid_table.put_item(Item=item)


def paid_customer_info(nmi, user_name):
    response = users_paid_table.get_item(Key={'nmi': nmi})
    if 'Item' in response:
        item = response["Item"]
        ## user_name = coginto_user()["user_name"]
        if item["user_name"] != user_name:
           return {"is_paid": False}

        if "charge_id" in item:
            charge_id = response["Item"]["charge_id"]
            charge = stripe.Charge.retrieve(charge_id)
            return {
                "is_paid": charge["paid"],
                "receipt": charge["receipt_url"],
                "amount": charge["amount"] / 100,
                "payment_date": item["creation_date"]
            }
        elif "coupon" in item:
            payment = item["payment"]
            return {
                "is_paid": payment["paid"],
                "receipt": payment["receipt_url"],
                "amount": float(payment["amount"]),
                "payment_date": item["creation_date"]
            }
    return {"is_paid": False}

## @app.route("/payment/charge", methods=["POST"])
def charge_client(stripe_token, nmi , user_name, user_email):
    ## params = request.get_json()
    ## token = params.get("stripeToken")
    ## nmi = params.get("nmi")
    ## user_ = coginto_user()

    customer = stripe.Customer.create(
        email=user_email,
        name= user_name,
        source=stripe_token,
        description=nmi
    )
    charge = stripe.Charge.create(
        customer=customer.id,
        amount=AMOUNT,
        currency='aud',
        description=f'BeatYourBill annual fee for NMI: {nmi} [including ($2.73) gst]',
        receipt_email=user_email,
        metadata={"nmi": nmi}
    )
    payment = json.loads(json.dumps(charge), parse_float=decimal.Decimal)
    populate_paid_users(nmi=nmi, user_email= user_email, user_name = user_name, payment=payment, customer_id=customer.id, charge_id= charge.id)
    return charge

def invoice_client(stripe_token, nmi , user_email):
    ## params = request.get_json()
    ## token = params.get("stripeToken")
    ## nmi = params.get("nmi")
    customer = stripe.Customer.create(
        email=user_email,
        source=stripe_token
    )
    stripe_cus = customer.id
    tax_rate = stripe.TaxRate.create(
        display_name='GST',
        jurisdiction='AUS',
        percentage=10.0
    )

    stripe.InvoiceItem.create(
        customer=stripe_cus,
        amount=AMOUNT,
        currency='aud',
        tax_rates=tax_rate,
        description=f'BeatYourBill annual fee for NMI: {nmi}'
    )
    invoice = stripe.Invoice.create(
        customer=stripe_cus,
        auto_advance=True
    )

    payment = json.loads(json.dumps(invoice), parse_float=decimal.Decimal)
    populate_paid_users(nmi, payment, stripe_cus, invoice.id)
    return invoice

def coupon_client( nmi , coupon, user_name, user_email):


    res = {
        "paid": None,
        "receipt_url": None,
        "status": None
    }
    if coupon and coupon == COUPON_TOKEN:
        res = {
            "paid": True,
            "receipt_url": "coupon",
            "status": "OK"
        }
        payment = dict(res)
        payment.update(
            {
                "coupon": coupon,
                "amount": decimal.Decimal(AMOUNT / 100)
            }
        )
        populate_paid_users(nmi=nmi, user_name = user_name, user_email=user_email, payment=payment, coupon=coupon)
    return res

## @app.route('/payment/is_paid', methods=['GET'])
def is_paid(nmi , user_name, customer_id):
    if not nmi:
        nmi = get_current_nmi(customer_id)
    payment = paid_customer_info(nmi, user_name)

    payment.update({"threshold": 100})
    return payment
