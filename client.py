import requests
from pprint import pprint
import json
import boto3

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
        url_ = "https://prodfree.beatyourbill.com.au/upload-file"
        payload = {"pdf":f}
        r = requests.post(url_, files=payload)
        if r.status_code == 200:
            result = json.loads(r.content)
            pprint(result)
            return result



if __name__=='__main__':

    file_name = "/home/agstudy/Downloads/1284602.pdf"
    result = upload_bill(file_name)
    print(result)



