# bot.py
import asyncio
import traceback
from typing import List
from core.utils import logger
import random
from core.utils import proxy_manager
from core.utils.account_manager import AccountManager
from core.utils.file_manager import file_to_list
from core.utils.proxy_manager import load_proxy


class Bot:
    def __init__(self, account_path, proxy_path, threads, ref_codes, captcha_service, delay_range):
        self.threads = threads
        self.ref_codes = ref_codes
        self.captcha_service = captcha_service
        self.account_manager = AccountManager(threads, ref_codes, captcha_service)
        self.should_stop = False
        self.accounts: List[str] = file_to_list(account_path)
        logger.success(f'Found {len(self.accounts)} accounts')
        load_proxy(proxy_path)
        logger.success(f'Found {len(proxy_manager.proxies)} proxies')
        self.delay_range = delay_range
        self.running_tasks = []

    async def process_account(self, account: str, action: str):
        email, password = account.split(':', 1)

        while not self.should_stop:
            result = await self.account_manager.process_account(email, password, action)
            if result is True and action == "mine":
                # For mining action, wait for 50 minutes before next cycle
                await asyncio.sleep(60 * 50)
            elif result is True:
                logger.info(f"{email} | Handled account!")
                break
            elif not result or result.get("result") is False:
                msg = " | "
                if isinstance(result, dict):
                    msg = f" | {result['msg']} | "

                logger.warning(f"{email} | {action.capitalize()} failed{msg}Retrying in 5 minutes.")
                await asyncio.sleep(300)  # Wait 5 minutes before retry

    async def start_action(self, action: str):
        logger.info(f"Starting {action} loop with slow start...")
        pending_accounts = self.accounts.copy()

        while pending_accounts and not self.should_stop:
            current_batch = []
            while len(current_batch) < self.threads and pending_accounts:
                account = pending_accounts.pop(0)
                email = account.split(':', 1)[0]
                delay = random.uniform(*self.delay_range)
                logger.info(f"{email} | waiting {delay:.2f} sec")
                await asyncio.sleep(delay)

                task = asyncio.create_task(self.process_account(account, action))
                current_batch.append(task)
                self.running_tasks.append(task)

            if current_batch:
                # Wait for the current batch to get past initial setup
                await asyncio.sleep(2)

        try:
            # Wait for all tasks to complete
            if self.running_tasks:
                await asyncio.gather(*self.running_tasks)
        except asyncio.CancelledError:
            pass
        finally:
            for task in self.running_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self.running_tasks, return_exceptions=True)
            logger.warning("All tasks completed or cleaned up")

    async def start_mining(self):
        await self.start_action("mine")

    async def start_registration(self):
        await self.start_action("register")

    def stop(self):
        # logger.info("Stopping Bot")
        self.should_stop = True
        self.account_manager.stop()
        for task in self.running_tasks:
            if not task.done():
                task.cancel()

