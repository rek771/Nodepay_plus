# account_manager.py
import asyncio
import traceback
import csv
import os
from datetime import datetime
import time

from faker import Faker
from core.utils import logger
from core.models.account import Account
from core.models.exceptions import CloudflareException, LoginError, MineError, TokenError
from core.nodepay_client import NodePayClient
from core.utils.file_manager import str_to_file
from core.utils.proxy_manager import get_proxy, release_proxy
from pyuseragents import random as random_useragent
import random


class AccountManager:
    def __init__(self, threads, ref_codes, captcha_service):
        self.ref_codes = ref_codes
        self.threads = threads
        self.fake = Faker()
        self.captcha_service = captcha_service
        self.should_stop = False
        self.earnings_file = 'data/earnings.csv'
        self.ensure_earnings_file_exists()
        self.counter = 0

    def ensure_earnings_file_exists(self):
        os.makedirs('data', exist_ok=True)
        if not os.path.exists(self.earnings_file):
            with open(self.earnings_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['Email', 'Last Update', 'Total Earnings'])

    def update_earnings(self, email: str, total_earning: float):
        temp_file = f'{self.earnings_file}.tmp'
        found = False
        
        # Read existing data
        rows = []
        try:
            with open(self.earnings_file, 'r', newline='') as f:
                reader = csv.reader(f)
                header = next(reader)  # Skip header
                rows = list(reader)
        except FileNotFoundError:
            header = ['Email', 'Last Update', 'Total Earnings']
            rows = []

        # Update or add new entry
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        for i, row in enumerate(rows):
            if row[0] == email:
                rows[i] = [email, current_time, str(total_earning)]
                found = True
                break
        
        if not found:
            rows.append([email, current_time, str(total_earning)])

        # Write updated data
        with open(temp_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(rows)

        # Replace original file
        os.replace(temp_file, self.earnings_file)
        # logger.info(f"Updated earnings for {email}: {total_earning}")

    @staticmethod
    async def create_account_session(email: str, password: str, proxy: str, captcha_service):
        client = NodePayClient(email=email, password=password, proxy=proxy, user_agent=random_useragent())
        uid, access_token = await client.get_auth_token(captcha_service)
        return Account(email, password, uid, access_token, client.user_agent, proxy)

    async def handle_session_error(self, account: Account, error: Exception):
        """Handle session-related errors and decide whether to recreate session"""
        logger.warning(f"{account.email} | Session error: {str(error)}")
        if account.proxy_url:
            await release_proxy(account.proxy_url)
        return await self.create_account_session(
            account.email,
            account.password,
            await get_proxy(),
            self.captcha_service
        )

    async def execute_action(self, account: Account, action: str, ref_code: str = None) -> bool:
        """Execute a given action ('register' or 'mine') and return success status"""
        client = NodePayClient(
            email=account.email,
            password=account.password,
            proxy=account.proxy_url,
            user_agent=account.user_agent
        )

        async with client:
            if action == "register":
                res = await client.register(ref_code, self.captcha_service)

                if res.get("success"):
                    logger.success(f'{account.email} | Registered')
                else:
                    logger.error(f'{account.email} | Registration failed | {res["msg"]}')
                    with open('failed_accounts.txt', 'a') as f:
                        f.write(f'{account.email}:{account.password}\n')

                    str_to_file('new_accounts.txt', f'{account.email}:{account.password}')
                return True
            elif action == "mine":
                if await client.ping(account.uid, account.access_token):
                    if not (self.counter % 5):  # Check earnings every 5th cycle
                        total_earning = await client.info(account.access_token)
                        self.update_earnings(account.email, total_earning)
                        logger.success(f"{account.email} | Mine | Points: {total_earning}")
                    else:
                        logger.success(f"{account.email} | Mine")
                    return True


    async def process_account(self, email: str, password: str, action: str):
        """Process account with automatic session management and error handling"""
        try:
            ref_code = None

            if action == "mine":
                # Initial session creation for mining
                account = await self.create_account_session(
                    email, password,
                    await get_proxy(),
                    self.captcha_service
                )
            else:
                # For registration, do not create a session (no login)
                account = Account(
                    email=email,
                    password=password,
                    uid=None,
                    access_token=None,
                    user_agent=random_useragent(),
                    proxy_url=await get_proxy()
                )

                ref_code = random.choice(
                    self.ref_codes or [
                        'leuskp97adNcZLs',
                        'VNhYgLnOjp5lZg9',
                        '3zYqqXiWTMR1qRH'
                    ]
                )

            for _ in range(3):
                try:
                    if await self.execute_action(account, action, ref_code):
                        return True
                    await asyncio.sleep(random.uniform(2, 5))  # Small delay between cycles
                    self.counter += 1
                except CloudflareException as e:
                    # logger.error(f"{email} | Cloudflare error: {str(e)}")
                    return {"result": False, "msg": str(e)}
                except TokenError as e:
                    account = await self.handle_session_error(account, e)
                except Exception as e:
                    logger.error(f"{email} | Unexpected error: {str(e)}")
                    logger.debug(traceback.format_exc())
                    break

        except LoginError as e:
            logger.warning(f"{email} | Login error: {str(e)}")
            return True
        except CloudflareException as e:
            logger.error(f"{email} | Cloudflare error: {str(e)}")
            return True
        except Exception as e:
            logger.error(f"Unexpected error {email}: {str(e)}")
            # logger.debug(traceback.format_exc())

    def stop(self):
        # logger.info("Stopping AccountManager")
        self.should_stop = True




