import boto3
if __name__=="__main__":
    cognito = boto3.client('cognito-idp')

    response = cognito.list_users(
        UserPoolId='ap-southeast-2_IG69RgQQJ',
        Filter='cognito:user_status="UNCONFIRMED"'
    )
    for k,v in response.items():
        from pprint import pprint
        for x in v :
            if "Attributes" in x:
                print(x["Attributes"][2]["Value"],x["Username"])
                cognito.admin_confirm_sign_up(
                    UserPoolId='ap-southeast-2_IG69RgQQJ',
                    Username=x["Username"])
