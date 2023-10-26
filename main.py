from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
import logging
from telegram.ext import CallbackContext, CommandHandler, filters, Application, CallbackQueryHandler, MessageHandler, JobQueue, ConversationHandler
import asyncio
import pickle
import time
from web3.gas_strategies.rpc import rpc_gas_price_strategy
from web3 import Web3
from web3.middleware import construct_sign_and_send_raw_middleware
from solcx import compile_source
from encrypt import *
from contracts import deploy_fundraiser, deposit
from os import system

CHOICE, PASSWORD, PASSWORDAGAIN, CREATEAGAIN, IMPORT, IMPORTEDPASS, IMPORTEDPASSAGAIN = range(7)
TOADDRESS, AMOUNT, WITHDRAWPASSWORD = range(3)
GOTPASS = range(1)
FUNDAMOUNT, FUNDLIMIT, FUNDPASSWORD = range(3)
tg_token = 'YOUR_TG_TOKEN'
w3 = Web3(Web3.HTTPProvider("https://rpc.apothem.network"))
w3.eth.set_gas_price_strategy(rpc_gas_price_strategy)
special_characters = ["!", "@", "#", "$", "%", "^", "&", "*", "(", ")"]

# Initialize db
try:
    users = pickle.load(open('storage/users.pkl', 'rb'))
except FileNotFoundError:
    users = {}
try:
    pending_tips = pickle.load(open('storage/pending_tips.pkl', 'rb'))
except FileNotFoundError:
    pending_tips = {}
try:
    pending_funds = pickle.load(open('storage/pending_tips.pkl', 'rb'))
except FileNotFoundError:
    pending_funds = {}

# Set up logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# Blockchain functions

# Send XDC transaction from 
def send_tx(address, to, amount, gas_price):
    tx_hash = w3.eth.send_transaction({
        'from': address,
        'to': to,
        'gas': 35000,
        'value': int(amount*10**18),
        'gasPrice': gas_price,
    })
    return tx_hash

# Transfer balance
def transfer_balance(from_priv, to_address):
    sender = w3.eth.account.from_key(from_priv)
    balance = w3.eth.get_balance(sender.address)
    tx_params = {
        'to':  to_address,
        'from': sender.address, 
        'value': int(balance),
        }
    gas_fee = w3.eth.estimate_gas(tx_params)
    print(balance)
    gas_price = w3.eth.generate_gas_price(tx_params)
    gas_value = int(gas_fee)*int(gas_price, 16)
    value_to_send = balance - int(gas_value*2)
    print(str(value_to_send))
    w3.middleware_onion.add(construct_sign_and_send_raw_middleware(sender))
    tx_hash = send_tx(sender.address, to_address, value_to_send/10**18, gas_price)
    return tx_hash

# Telegram bot functions

# The /start command
async def start(update: Update, context: CallbackContext) -> None:
    if update.effective_chat.type != 'private':
        return
    await update.message.reply_text("Hello! I am the XDC tipping bot!\nCreate a /newwallet first", parse_mode='html')
    return

# Test function (/send <privkey_from> <address_to> <amount>)
async def send(update: Update, context: CallbackContext) -> None:
    address = context.args[0]
    to = context.args[1]
    amount = context.args[2]
    hash = send_tx(address, to, amount, 10000000000)
    await update.message.reply_text("Transaction sent! Tx Hash: <code>" + str(hash) + "</code>", parse_mode='html')
    await asyncio.sleep(0.05)
    return

# The /mywallet command that users will invoke in the XDC group
async def show_user_wallet(update: Update, context: CallbackContext) -> None:
    if update.effective_chat.type != 'private':
        return
    user = update.message.from_user.id
    if user in users.keys():
        address = users[user]['address']
        balance = w3.eth.get_balance(address)
        await update.message.reply_text(f'Your address: <code>{users[user]["address"]}</code>\nYour balance: {balance/10**18} XDC', parse_mode='html')
        await asyncio.sleep(0.05)
        return
    else:
        await update.message.reply_text('You have no wallet yet!\nCreate one with /start')
        await asyncio.sleep(0.05)
        return   

# The /tip <amount> command that users will invoke in the XDC group
async def tip(update: Update, context: CallbackContext) -> None:
    global users
    group_id = update.effective_chat.id
    try:
        to_user = update.effective_message.reply_to_message.from_user.id
    except AttributeError:
        await update.message.reply_text('You need to reply to a user!')
        await asyncio.sleep(0.05)
        return
    from_user = update.effective_message.from_user.id
    try:
        amount = float(context.args[0])
    except IndexError:
        await update.message.reply_text('Say /tip <amount> in response to the person you want to tip')
        await asyncio.sleep(0.05)
        return
    except ValueError:
        if ',' in context.args[0]:
            amount = context.args[0].replace(',', '.')
            try:
                amount = float(amount)
            except ValueError:
                await update.message.reply_text('That is not a number!')
                await asyncio.sleep(0.05)
                return    
        else:
            await update.message.reply_text('That is not a valid tip amount!')
            await asyncio.sleep(0.05)
            return
    if amount <= 0:
        await update.message.reply_text('Amount must be positive!')
        await asyncio.sleep(0.05)
        return
    if to_user not in users.keys():
        await update.message.reply_text('This user hasn\'t created a wallet yet!')
        await asyncio.sleep(0.05)
        return
    if from_user not in users.keys():
        await update.message.reply_text('You haven\'t created a wallet yet!')
        await asyncio.sleep(0.05)
        return
    pending_tips[from_user] = {'to': to_user, 'amount': amount, 'group': group_id}
    await update.message.reply_text('I sent you a DM for confirmation!')
    await context.bot.send_message(text='Hey! reply with your password to finish the process:', chat_id=from_user)
    await asyncio.sleep(0.05)
    return

# Function that handles the tip process (when the user replies with his password)
async def check_process(update: Update, context: CallbackContext) -> None:  
    from_user_id = update.message.from_user.id
    if update.effective_chat.type != 'private':
        return
    if update.message.from_user.id not in pending_tips.keys() and update.message.from_user.id not in pending_funds.keys():
        return
    if update.message.from_user.id in pending_tips.keys():  
        from_user_id = update.message.from_user.id
        to_user_id = pending_tips[from_user_id]['to']
        amount = pending_tips[from_user_id]['amount']
        password = update.message.text
        # Verify password and get priv key
        token = users[from_user_id]['encrypted_key']
        try:
            priv_key = password_decrypt(token, password)
        except Exception:
            await update.message.reply_text('Wrong password! Tip failed.')
            await asyncio.sleep(0.05)
            del pending_tips[from_user_id]
            return
        acc = w3.eth.account.from_key(priv_key)
        w3.middleware_onion.add(construct_sign_and_send_raw_middleware(acc))
        try:
            hash = send_tx(users[from_user_id]['address'], users[to_user_id]['address'], amount, 10000000000)
        except Exception as exec:
            if 'insufficient funds' in str(exec):
                await update.message.reply_text('Not enough XDC! Tip failed.')
                await asyncio.sleep(0.05)
                del pending_tips[from_user_id]
                return
        hash = w3.to_hex(hash)
        from_member = await context.bot.get_chat_member(pending_tips[from_user_id]['group'], from_user_id)
        await asyncio.sleep(0.05)
        to_member = await context.bot.get_chat_member(pending_tips[from_user_id]['group'], to_user_id)
        await asyncio.sleep(0.05)
        await update.message.reply_text(f'Transaction sent! Tx Hash: <code>{str(hash)}</code>\n<a href="https://explorer.apothem.network/txs/{str(hash)}">Block explorer</a>', parse_mode='html')
        await asyncio.sleep(0.05)
        await context.bot.send_message(text=f'{from_member.user.first_name} succesfully tipped {amount}XDC to {to_member.user.first_name}!\n<a href="https://explorer.apothem.network/txs/{str(hash)}">Check on block explorer.</a>', parse_mode='html', chat_id=pending_tips[from_user_id]['group'])
        await asyncio.sleep(0.05)
        del pending_tips[from_user_id]
        return
    if update.message.from_user.id in pending_funds.keys():
        password = update.message.text
        # Verify password and get priv key
        token = users[from_user_id]['encrypted_key']
        try:
            priv_key = password_decrypt(token, password)
        except Exception:
            await update.message.reply_text('Wrong password! Fund failed.')
            await asyncio.sleep(0.05)
            del pending_funds[from_user_id]
            return
        funding_data = pickle.load(open(f'contracts/fundraiser{pending_funds[from_user_id]["to"]}.pkl', 'rb'))
        amount = pending_funds[from_user_id]['amount']
        print(amount)
        acc = w3.eth.account.from_key(priv_key)
        print(acc)
        w3.middleware_onion.add(construct_sign_and_send_raw_middleware(acc))
        contract_address = funding_data['address']
        print(contract_address)
        abi = funding_data['abi']
        print(abi)
        result = deposit(priv_key, abi, amount, contract_address)
        if result == 'insufficient':
            await update.message.reply_text('Not enough XDC! fund failed.')
            await asyncio.sleep(0.05)
            del pending_funds[from_user_id]
            return
        if result == 'reverted':
            await update.message.reply_text('Execution reverted!')
            await asyncio.sleep(0.05)
            del pending_funds[from_user_id]
            return
        if result == 'late':
            await update.message.reply_text('Too late! fund finished already.')
            await asyncio.sleep(0.05)
            del pending_funds[from_user_id]
            return
        await update.message.reply_text('Transaction sent! Tx Hash: <code>' + str(result) + '</code>\n<a href="https://explorer.apothem.network/txs/' + str(result) + '">Block explorer</a>', parse_mode='html')
        await asyncio.sleep(0.05)
        del pending_funds[from_user_id]
        return

# Functions that handle the creation process of the wallet
async def create_wallet(update: Update, context: CallbackContext) -> None:
    if update.effective_chat.type != 'private':
        return
    if update.message.from_user.id in users.keys():
        await update.message.reply_text('You already have a wallet!\nDo you want to create a new one?', reply_markup=ReplyKeyboardMarkup([['Yes', 'No']], one_time_keyboard=True))
        await asyncio.sleep(0.05)
        return CREATEAGAIN
    else:
        await update.message.reply_text('What do you want to do?', reply_markup=ReplyKeyboardMarkup([['Create wallet', 'Import wallet']], one_time_keyboard=True))
        await asyncio.sleep(0.05)
        return CHOICE

async def chosen_option(update: Update, context: CallbackContext) -> None:
    option = update.message.text
    if 'create' in option.lower():
        await update.message.reply_text('Ok! now choose a password to encrypt your new wallet:')
        asyncio.sleep(0.05)
        return PASSWORD
    elif 'import' in option.lower():
        await update.message.reply_text('Ok! now send me the private key for your wallet:')
        await asyncio.sleep(0.05)
        return IMPORT
    else:
        await update.message.reply_text('That is not a valid response!')
        return ConversationHandler.END
        

async def password_once(update: Update, context: CallbackContext) -> None:
    pass1 = update.message.text
    if pass1 == 'STOP':
        await update.message.reply_text("Ok, have a nice day and enjoy XDC!")
        await asyncio.sleep(0.05)
        return ConversationHandler.END
    if len(pass1) < 8:
        await update.message.reply_text('Password must be at least 8 characters long!\nReply with a new one or say STOP to stop the process.')
        await asyncio.sleep(0.05)
        return PASSWORD
    if not any(character in special_characters for character in pass1):
        await update.message.reply_text('Password must contain at least one special character!\nReply with a new one or say STOP to stop the process.')
        await asyncio.sleep(0.05)
        return PASSWORD
    await update.message.reply_text('Write it again to confirm:')
    await asyncio.sleep(0.05)
    context.user_data['pass1'] = pass1
    return PASSWORDAGAIN

async def password_twice(update: Update, context: CallbackContext) -> None:
    global users
    if update.message.text != context.user_data['pass1']:
        await update.message.reply_text('Passwords don\'t match!\nReply with a new one or say STOP to stop the process.')
        await asyncio.sleep(0.05)
        return PASSWORD
    # CREATE WALLET and store private key encrypted with password
    user_id = update.message.from_user.id
    acc = w3.eth.account.create()
    address = acc.address
    private_key = password_encrypt(acc._private_key, context.user_data['pass1'])
    # transfer balance
    users[user_id] = {'address': address, 'encrypted_key': private_key}
    pickle.dump(users, open('storage/users.pkl', 'wb'))
    await update.message.reply_text(text=f'Address: <code>{address}</code>\n\nRemember to delete the password messages!', parse_mode='html')
    await asyncio.sleep(0.05)
    return ConversationHandler.END

async def create_wallet_again(update: Update, context: CallbackContext) -> None:
    choice = update.message.text
    if choice.lower() == 'no':
        await update.message.reply_text("Ok, have a nice day and enjoy XDC!")
        await asyncio.sleep(0.05)
        return ConversationHandler.END
    else:
        await update.message.reply_text(text="Old wallet deleted!\nWhat do you want to do?", reply_markup=ReplyKeyboardMarkup([['Create wallet', 'Import wallet']], one_time_keyboard=True))
        await asyncio.sleep(0.05)
        return CHOICE

async def process_privkey(update: Update, context: CallbackContext) -> None:
    priv = update.message.text
    context.user_data['priv'] = priv
    try:
        w3.eth.account.from_key(priv)
    except Exception:
        await update.message.reply_text(text='Not a valid private key!\nSay /newwallet to try again.')
        await asyncio.sleep(0.05)
        return ConversationHandler.END
    await update.message.reply_text('Ok! now choose a password to encrypt your new wallet:')
    await asyncio.sleep(0.05)
    return IMPORTEDPASS

async def password_once_import(update: Update, context: CallbackContext) -> None:
    pass1 = update.message.text
    if pass1 == 'STOP':
        await update.message.reply_text("Ok, have a nice day and enjoy XDC!")
        await asyncio.sleep(0.05)
        return ConversationHandler.END
    if len(pass1) < 8:
        await update.message.reply_text('Password must be at least 8 characters long!\nReply with a new one or say STOP to stop the process.')
        await asyncio.sleep(0.05)
        return IMPORTEDPASS
    if not any(character in special_characters for character in pass1):
        await update.message.reply_text('Password must contain at least one special character!\nReply with a new one or say STOP to stop the process.')
        await asyncio.sleep(0.05)
        return IMPORTEDPASS
    await update.message.reply_text('Write it again to confirm:')
    await asyncio.sleep(0.05)
    context.user_data['pass1'] = pass1
    return IMPORTEDPASSAGAIN

async def password_twice_import(update: Update, context: CallbackContext) -> None:
    global users
    if update.message.text != context.user_data['pass1']:
        await update.message.reply_text('Passwords don\'t match!\nReply with a new one or say STOP to stop the process.')
        await asyncio.sleep(0.05)
        return IMPORTEDPASS
    import_wallet(context.user_data['priv'], context.user_data['pass1'], update.message.from_user.id)
    await update.message.reply_text(text=f'Address: <code>{users[update.message.from_user.id]["address"]}</code>\n\nRemember to delete the password messages!', parse_mode='html')
    await asyncio.sleep(0.05)
    return ConversationHandler.END


def import_wallet(priv, password, user_id) -> None:
    global users
    try:
        acc = w3.eth.account.from_key(priv)
    except Exception:
        return False
    encrypted_priv = password_encrypt(str.encode(priv), password)
    # Transfer balance
    users[user_id] = {'address': acc.address, 'encrypted_key': encrypted_priv}
    pickle.dump(users, open('storage/users.pkl', 'wb'))
    return True   
    
# Inline button handler
async def button(update: Update, context: CallbackContext) -> None:
    global users
    query = update.callback_query
    await query.answer()
    if query.data == "create":
        from_user = query.from_user.id
        acc = w3.eth.account.create()
        address = acc.address
        private_key = w3.to_hex(acc._private_key)
        users[from_user] = private_key
        pickle.dump(users, open('users.pkl', 'wb'))
        w3.middleware_onion.add(construct_sign_and_send_raw_middleware(acc))
        await context.bot.send_message(text=f'Address: <code>{address}</code>\nPrivate Key: <code>{private_key}</code>', chat_id=query.message.chat_id, parse_mode='html')
        await asyncio.sleep(0.05)
        return

async def withdraw_wallet(update: Update, context: CallbackContext) -> None:
    if update.effective_chat.type != 'private':
        return ConversationHandler.END
    await update.message.reply_text('Send me the recipient address:')
    await asyncio.sleep(0.05)
    return TOADDRESS

async def withdraw_to(update: Update, context: CallbackContext) -> None:
    if update.effective_chat.type != 'private':
        return
    to_address = update.message.text
    context.user_data['to_address'] = to_address
    if not to_address.startswith('0x') and len(to_address) != 42:
        await update.message.reply_text('Invalid address!')
        await asyncio.sleep(0.05)
        return ConversationHandler.END
    await update.message.reply_text('Send me the amount to withdraw (or ALL for all your XDC):')
    await asyncio.sleep(0.05)
    return AMOUNT

async def withdraw_amount(update: Update, context: CallbackContext) -> None:
    if update.effective_chat.type != 'private':
        return
    amount = update.message.text
    try:
        if amount.lower() == 'all':
            context.user_data['amount'] = amount
            await update.message.reply_text('Send me the password:')
            await asyncio.sleep(0.05)
            return WITHDRAWPASSWORD
        else:
            amount = float(amount)
    except ValueError:
        if ',' in amount:
            amount = context.args[0].replace(',', '.')
            try:
                amount = float(amount)
            except ValueError:
                await update.message.reply_text('That is not a number!')
                await asyncio.sleep(0.05)
                return ConversationHandler.END
        else:
            await update.message.reply_text('That is not a valid tip amount!')
            await asyncio.sleep(0.05)
            return ConversationHandler.END
    if amount <= 0:
        await update.message.reply_text('Amount must be positive!')
        await asyncio.sleep(0.05)
        return ConversationHandler.END
    context.user_data['amount'] = amount
    await update.message.reply_text('Send me the password:')
    await asyncio.sleep(0.05)
    return WITHDRAWPASSWORD

async def withdraw_password(update: Update, context: CallbackContext) -> None:
    if update.effective_chat.type != 'private':
        return ConversationHandler.END
    password = update.message.text
    if password == 'STOP':
        await update.message.reply_text('Ok! I\'ll stop here.')
        await asyncio.sleep(0.05)
        return ConversationHandler.END
    try:
        priv_key = password_decrypt(users[update.message.from_user.id]['encrypted_key'], password)
    except Exception:
        await update.message.reply_text('Wrong password! Try again or say STOP to cancel the operation.')
        await asyncio.sleep(0.05)
        return WITHDRAWPASSWORD
    if context.user_data['amount'] != 'all':
        acc = w3.eth.account.from_key(priv_key)
        w3.middleware_onion.add(construct_sign_and_send_raw_middleware(acc))
        result = send_tx(users[update.message.from_user.id]['address'], context.user_data['to_address'], context.user_data['amount'], 10000000000)
    else: 
        result = transfer_balance(priv_key, context.user_data['to_address'])
    await update.message.reply_text(f'Transaction sent!\n<a href="https://explorer.apothem.network/txs/{str(w3.to_hex(result))}">Here\'s the link</a>', parse_mode='html')
    await asyncio.sleep(0.05)
    return ConversationHandler.END

async def secret_convo(update: Update, context: CallbackContext) -> None:
    if update.effective_chat.type != 'private':
        return
    await update.message.reply_text('Send me the password:')
    await asyncio.sleep(0.05)
    return GOTPASS

async def secret_password(update: Update, context: CallbackContext) -> None:
    if update.effective_chat.type != 'private':
        return
    password = update.message.text
    if password == 'STOP':
        await update.message.reply_text('Ok! I\'ll stop here.')
        await asyncio.sleep(0.05)
        return ConversationHandler.END
    try:
        priv_key = password_decrypt(users[update.message.from_user.id]['encrypted_key'], password)
    except Exception:
        await update.message.reply_text('Wrong password! Try again or say STOP to cancel the operation.')
        await asyncio.sleep(0.05)
        return GOTPASS
    await update.message.reply_text(f'Private key: <code>{w3.to_hex(priv_key)}</code>', parse_mode='html')
    await asyncio.sleep(0.05)
    return ConversationHandler.END

async def start_fundraise_conversation(update: Update, context: CallbackContext) -> None:
    if update.effective_chat.type != 'private':
        return
    await update.message.reply_text('How many XDC would you like to raise?')
    await asyncio.sleep(0.05)
    return FUNDAMOUNT

async def fundraise_time_limit(update: Update, context: CallbackContext) -> None:
    if update.effective_chat.type != 'private':
        return
    try:
        amount = float(update.message.text)
    except ValueError:
        if ',' in amount:
            amount = context.args[0].replace(',', '.')
            try:
                amount = float(amount)
            except ValueError:
                await update.message.reply_text('That is not a number!')
                await asyncio.sleep(0.05)
                return ConversationHandler.END
        else:
            await update.message.reply_text('That is not a valid tip amount!')
            await asyncio.sleep(0.05)
            return ConversationHandler.END
    if amount <= 0:
        await update.message.reply_text('Amount must be positive!')
        await asyncio.sleep(0.05)
        return ConversationHandler.END
    context.user_data['fund_amount'] = update.message.text
    await update.message.reply_text('How long would you like to fundraise for? (in minutes)\nExample: 30')
    await asyncio.sleep(0.05)
    return FUNDLIMIT

async def fundraise_password(update: Update, context: CallbackContext) -> None:
    if update.effective_chat.type != 'private':
        return
    try:
        int(update.message.text)
    except ValueError:
        await update.message.reply_text('That is not a number! Try again')
        await asyncio.sleep(0.05)
        return FUNDLIMIT
    context.user_data['fund_limit'] = update.message.text
    await update.message.reply_text('Now enter your password:')
    await asyncio.sleep(0.05)
    return FUNDPASSWORD

async def fundraise_deploy(update: Update, context: CallbackContext) -> None:
    if update.effective_chat.type != 'private':
        return
    password = update.message.text
    if password == 'STOP':
        await update.message.reply_text('Ok! I\'ll stop here.')
        await asyncio.sleep(0.05)
        return ConversationHandler.END
    try:
        priv_key = password_decrypt(users[update.message.from_user.id]['encrypted_key'], password)
    except Exception:
        await update.message.reply_text('Wrong password! Try again or say STOP to cancel the operation.')
        await asyncio.sleep(0.05)
        return FUNDPASSWORD
    context.job_queue.run_once(deploy_fundraiser, when=0.5, data={'private_key': priv_key, 'fundraise_amount': context.user_data['fund_amount'], 'ending_time': context.user_data['fund_limit']}, chat_id=update.effective_chat.id)
    await update.message.reply_text('Ok! I\'ll start deploying the fundraiser!')
    await asyncio.sleep(0.05)
    return ConversationHandler.END

async def announce(update: Update, context: CallbackContext) -> None:
    if update.effective_chat.type == 'private':
        return
    description = update.message.text.split('/announce ')[1]
    try:
        fundraise_data = pickle.load(open(f'contracts/fundraiser{update.effective_message.from_user.id}.pkl', 'rb'))
    except FileNotFoundError:
        await update.message.reply_text('You have not started a fundraiser yet!')
        await asyncio.sleep(0.05)
        return
    if fundraise_data['ending_time'] < time.time():
        await update.message.reply_text('Your fundraiser has ended!')
        
        await asyncio.sleep(0.05)
        return
    msg = await update.message.reply_text(f'New fundraiser is live!\n<b>Amount:</b> {fundraise_data["fundraise_amount"]} XDC\n<b>Ending time:</b> {time.strftime("%Hh %Mm %Ss", time.gmtime(fundraise_data["ending_time"]))} UTC\n<b>Description:</b> {description}\n\nReply to this message with /fund [amount] to fundraise this proposal.', parse_mode='html')
    try:
        fundraiser_replies = pickle.load(open(f'storage/fundraiser_replies.pkl', 'rb'))
    except FileNotFoundError:
        fundraiser_replies = {}
    fundraiser_replies[msg.id] = update.message.from_user.id
    pickle.dump(fundraiser_replies, open(f'storage/fundraiser_replies.pkl', 'wb'))
    await asyncio.sleep(0.05)
    return

async def fund(update: Update, context: CallbackContext) -> None:
    global users
    group_id = update.effective_chat.id
    try:
        fundraiser_replies = pickle.load(open(f'storage/fundraiser_replies.pkl', 'rb'))
    except FileNotFoundError:
        await update.message.reply_text('You need to reply to a fundraiser announcement!')
        await asyncio.sleep(0.05)
        return
    try:
        to_msg = update.effective_message.reply_to_message.id
    except AttributeError:
        await update.message.reply_text('You need to reply to a fundraiser announcement!')
        await asyncio.sleep(0.05)
        return
    if to_msg not in fundraiser_replies.keys():
        await update.message.reply_text('You need to reply to a fundraiser announcement!')
        await asyncio.sleep(0.05)
        return
    from_user = update.effective_message.from_user.id
    to_user = fundraiser_replies[to_msg]
    try:
        amount = float(context.args[0])
    except IndexError:
        await update.message.reply_text('Say /fund <amount> in response to the fundraiser announcement')
        await asyncio.sleep(0.05)
        return
    except ValueError:
        if ',' in context.args[0]:
            amount = context.args[0].replace(',', '.')
            try:
                amount = float(amount)
            except ValueError:
                await update.message.reply_text('That is not a number!')
                await asyncio.sleep(0.05)
                return    
        else:
            await update.message.reply_text('That is not a valid amount!')
            await asyncio.sleep(0.05)
            return
    if amount <= 0:
        await update.message.reply_text('Amount must be positive!')
        await asyncio.sleep(0.05)
        return
    try:
        fundraise_data = pickle.load(open(f'contracts/fundraiser{to_user}.pkl', 'rb'))
    except FileNotFoundError:
        await update.message.reply_text('This user hasn\'t created a fundraiser yet!')
        await asyncio.sleep(0.05)
        return
    if from_user not in users.keys():
        await update.message.reply_text('You haven\'t created a wallet yet!')
        await asyncio.sleep(0.05)
        return
    pending_funds[from_user] = {'to': to_user, 'amount': amount, 'group': group_id, 'fundraiser_data': fundraise_data}
    await update.message.reply_text('I sent you a DM for confirmation!')
    await context.bot.send_message(text='Hey! reply with your password to finish the process:', chat_id=from_user)
    await asyncio.sleep(0.05)
    return


# Set up and start the bot
def main() -> None:
    wallet_conversation = ConversationHandler(
        entry_points=[CommandHandler('newwallet', create_wallet)],
        states={
            CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, chosen_option)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, password_once)],
            PASSWORDAGAIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, password_twice)],
            CREATEAGAIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, create_wallet_again)],
            IMPORT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_privkey)],
            IMPORTEDPASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, password_once_import)],
            IMPORTEDPASSAGAIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, password_twice_import)]
        },
        fallbacks=[],
        allow_reentry=True
        
    )
    
    withdraw_conversation = ConversationHandler(
        entry_points=[CommandHandler('withdraw', withdraw_wallet)],
        states={
            TOADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_to)],
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_amount)],
            WITHDRAWPASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_password)],
        },
        fallbacks=[],
        allow_reentry=True
    )
    
    secret_conversation = ConversationHandler(
        entry_points=[CommandHandler('secret', secret_convo)],
        states={
            GOTPASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, secret_password)]
        },
        fallbacks=[],
        allow_reentry=True
    )
    
    fundraiser_conversation = ConversationHandler(
        entry_points=[CommandHandler('fundraise', start_fundraise_conversation)],
        states={
            FUNDAMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, fundraise_time_limit)],
            FUNDLIMIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, fundraise_password)],
            FUNDPASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, fundraise_deploy)],
        },
        fallbacks=[],
        allow_reentry=True
    )
    
    application = Application.builder().token(tg_token).build() 
    application.add_handler(CommandHandler('start', start)) 
    application.add_handler(CommandHandler('send', send))
    application.add_handler(CommandHandler('tip', tip))
    application.add_handler(CommandHandler('fund', fund))
    application.add_handler(CommandHandler('announce', announce))
    application.add_handler(withdraw_conversation)
    application.add_handler(wallet_conversation)
    application.add_handler(secret_conversation)
    application.add_handler(fundraiser_conversation)
    application.add_handler(CommandHandler('mywallet', show_user_wallet))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, check_process))
    application.add_handler(CallbackQueryHandler(button))
    application.run_polling(allowed_updates=Update.ALL_TYPES)

# Main
if __name__ == "__main__":
    main()
