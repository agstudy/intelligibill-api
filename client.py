import requests
from pprint import pprint
import json


def retrieve_bests_by_id(id):
    try:
        url_ = "https://free.beatyourbill.com.au/retrieve-upload-bests"
        payload = {"upload_id":id}
        r = requests.post(url_, data=payload)
        if r.status_code == 200:
            result = json.loads(r.content)
            pprint(result)
        else:
            print(r)
    except Exception as ex:
        print(ex)

def search_bests_by_id(id):
    try:
        url_ = "https://free.beatyourbill.com.au/search-upload-bests"
        payload = {"upload_id":id}
        r = requests.post(url_, data=payload)
        if r.status_code == 200:
            result = json.loads(r.content)
            pprint(result)
        else:
            print(r)
    except Exception as ex:
        print(ex)

def upload_bill(file_name):
    with open(file_name, "rb") as f:
        url_ = "https://free.beatyourbill.com.au/upload-file"
        payload = {"pdf":f}
        r = requests.post(url_, files=payload)
        if r.status_code == 200:
            result = json.loads(r.content)
            pprint(result)
            return result


if __name__=='__main__':
    # retrieve_bests_by_id("bill-47e3f6e2-3c5f-11ea-8ddc-e6d93601058f")
    # file_name = "/tmp/eric_lader/20015759338_2019-12-19.pdf"
    # result = upload_bill(file_name)
    # search_bests_by_id(result["upload_id"])
    # retrieve_bests_by_id(result["upload_id"])

    import boto3
    ssm = boto3.client('ssm', region_name='us-east-1')
    parameter = ssm.get_parameter(Name='stripe_key')
    print(parameter["Parameter"]["Value"])