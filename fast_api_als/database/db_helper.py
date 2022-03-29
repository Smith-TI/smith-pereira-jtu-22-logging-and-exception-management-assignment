import uuid
import logging

import boto3
from boto3.dynamodb.conditions import Key

from fast_api_als import constants
from fast_api_als.utils.boto3_utils import get_boto3_session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)0.8s] %(message)s",
)
logger = logging.getLogger(__name__)


class DBHelper:
    def __init__(self, session: boto3.session.Session):
        self.session = session
        self.ddb_resource = session.resource('dynamodb')
        self.table = self.ddb_resource.Table(constants.DB_TABLE_NAME)

    def insert_lead(self, lead_hash: str, lead_provider: str, response: str):
        logger.info(f"Inserting lead from {lead_provider} with response as {response}")
        item = {
            'pk': f'LEAD#{lead_hash}',
            'sk': lead_provider,
            'response': response
        }
        res = self.table.put_item(Item=item)
        verify_add_entry_response(res, f"{lead_provider}+'-'+{lead_hash}")

    def insert_oem_lead(self, uuid: str, make: str, model: str, date: str, email: str, phone: str, last_name: str,
                        timestamp: str, make_model_filter_status: str, lead_hash: str):

        item = {
            'pk': f"{make}#{uuid}",
            'sk': f"{make}#{model}" if make_model_filter_status else "",
            'gsipk': f"{make}#{date}",
            'gsisk': "0#0",
            'make': make,
            'model': model,
            'email': email,
            'phone': phone,
            'last_name': last_name,
            'timestamp': timestamp,
            'conversion_status': "0",
            "make_model_filter_status": make_model_filter_status,
            "lead_hash": lead_hash
        }

        response = self.table.put_item(Item=item)
        verify_add_entry_response(response, f"{make}#{email}#{phone}#{last_name}")

    def check_duplicate_api_call(self, lead_hash: str, lead_provider: str):
        res = self.table.get_item(
            Key={
                'pk': f"LEAD#{lead_hash}",
                'sk': lead_provider
            }
        )
        item = res.get('Item')
        if not item:
            return False, ""
        else:
            return True, item['response']

    def accepted_lead_not_sent_for_oem(self, oem: str, date: str):
        res = self.table.query(
            IndexName='gsi-index',
            KeyConditionExpression=Key('gsipk').eq(f"{oem}#{date}")
                                   & Key('gsisk').begins_with("0#0")
        )
        return res.get('Items', [])

    def update_lead_conversion_status(self, uuid: str, oem: str, make: str, model: str):
        res = self.table.get_item(
            Key={
                'pk': f"{uuid}#{oem}"
            }
        )
        item = res['Item']
        if not item:
            logger.info(f"No item found for {uuid}#{oem}#{make}#{model}")
            return False
        item['gsisk'] = item['gsisk'][0] + "#" + "1"
        res = self.table.put_item(Item=item)
        verify_add_entry_response(res, f"{uuid}#{oem}#{make}#{model}")
        return True

    def update_lead_sent_status(self, uuid: str, oem: str, make: str, model: str):
        res = self.table.get_item(
            Key={
                'pk': f"{uuid}#{oem}"
            }
        )
        item = res['Item']
        if not item:
            logger.info(f"No item found for {uuid}#{oem}#{make}#{model}")
            return False
        item['gsisk'] = "1#0"
        res = self.table.put_item(Item=item)
        verify_add_entry_response(res, f"{uuid}#{oem}#{make}#{model}")
        return True

    def get_make_model_filter_status(self, oem: str):
        res = self.table.get_item(
            Key={
                'pk': f"OEM#{oem}",
                'sk': 'METADATA'
            }
        )
        verify_add_entry_response(res, oem)
        if res['Item'].get('settings', {}).get('make_model', "False") == 'True':
            return True
        return False

    def verify_api_key(self, apikey: str):
        res = self.table.query(
            IndexName='gsi-index',
            KeyConditionExpression=Key('gsipk').eq(apikey)
        )
        item = res.get('Items', [])
        if len(item) == 0:
            return False
        return True

    def register_3PL(self, username: str):
        res = self.table.query(
            KeyConditionExpression=Key('pk').eq(username)
        )
        item = res.get('Items', [])
        if len(item):
            return None
        apikey = uuid.uuid4().hex
        res = self.table.put_item(
            Item={
                'pk': username,
                'sk': apikey,
                'gsipk': apikey,
                'gsisk': username
            }
        )
        return apikey

    def insert_customer_lead(self, uuid: str, email: str, phone: str, last_name: str, make: str, model: str):
        item = {
            'pk': uuid,
            'sk': 'CUSTOMER_LEAD',
            'gsipk': email,
            'gsisk': uuid,
            'gsipk1': f"{phone}#{last_name}",
            'gsisk1': uuid,
            'oem': make,
            'make': make,
            'model': model
        }
        res = self.table.put_item(Item=item)
        verify_add_entry_response(res, f"{uuid}#{email}#{phone}")

    def lead_exists(self, uuid: str, make: str, model: str):
        lead_exist = False
        if self.get_make_model_filter_status(make):
            res = self.table.query(
                KeyConditionExpression=Key('pk').eq(f"{uuid}#{make}") & Key('sk').eq(f"{make}#{model}")
            )
            if len(res['Items']):
                lead_exist = True
        else:
            res = self.table.query(
                KeyConditionExpression=Key('pk').eq(f"{uuid}#{make}")
            )
            if len(res['Items']):
                lead_exist = True
        return lead_exist

    def check_duplicate_lead(self, email: str, phone: str, last_name: str, make: str, model: str):
        email_attached_leads = self.table.query(
            IndexName='gsi-index',
            KeyConditionExpression=Key('gsipk').eq(email)
        )
        phone_attached_leads = self.table.query(
            IndexName='gsi1-index',
            KeyConditionExpression=Key('gsipk1').eq(f"{phone}#{last_name}")
        )
        customer_leads = set(email_attached_leads['Items'])
        for item in phone_attached_leads['Items']:
            customer_leads.add(item)

        for item in customer_leads:
            if self.lead_exists(item['pk'], make, model):
                return True
        logger.info(f"No duplicate lead for {email}#{phone}#{last_name}")
        return False



def verify_add_entry_response(response, data):
    status_code = response['ResponseMetadata']['HTTPStatusCode']
    if not status_code == 200:
        logger.error(f"Failed to add {data} to the database.")
    else:
        logger.info("New entry added successfully.")


session = get_boto3_session()
db_helper_session = DBHelper(session)