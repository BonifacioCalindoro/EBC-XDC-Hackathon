from solcx import install_solc, compile_source
from telegram.ext import CallbackContext
from web3 import Web3
import pickle
import time
from web3.middleware import construct_sign_and_send_raw_middleware, geth_poa_middleware
from web3.gas_strategies.rpc import rpc_gas_price_strategy
import asyncio
async def deploy_fundraiser(context: CallbackContext):
    private_key = context.job.data['private_key']
    fundraise_amount = context.job.data['fundraise_amount']
    ending_time = context.job.data['ending_time']
    install_solc('0.8.9')
    compiled_sol = compile_source(
    '''
    // SPDX-License-Identifier: UNLICENSED
    pragma solidity 0.8.9;


    contract Fundraiser {

        enum Status {
            Ongoing, Guaranteed, Failed, Successful
        }

        bool lock = false;

        address payable beneficiary;

        event Commitment(address who, uint amount, uint when);
        event Withdrawal(address who, uint amount, uint when);


        mapping (address => uint256) commitFunds;

        uint256 totalFunds = 0;
        uint256 constant decimalFactors = 10 **18;
        uint256 immutable public amount;
        uint256 immutable public deadline;


        constructor(  uint _amount, uint _fundraisingTime )  {

            beneficiary = payable(msg.sender);
            amount = _amount*decimalFactors;
            deadline = block.timestamp + _fundraisingTime;
            
        }

        function status() public view returns (Status) {

            if (block.timestamp <= deadline) {
                if (totalFunds < amount) {
                    return Status.Ongoing;
                    } else {return Status.Guaranteed; }
            } else {
                if (totalFunds < amount) {
                    return Status.Failed;
                } else {
                    return Status.Successful;
                }
            
            }

        }

        function deposit() public payable {
            
            require(block.timestamp <= deadline, "Too late");
            commitFunds[msg.sender] += msg.value;
            totalFunds += msg.value;

            emit Commitment(msg.sender, msg.value, block.timestamp);

        }

        function withdraw() public {

            require(lock == false);
            lock = true;
            require(status()==Status.Failed, "Can't Withdraw");
            (bool sent) = payable(msg.sender).send(commitFunds[msg.sender]);

            if (sent) {emit Withdrawal(msg.sender, commitFunds[msg.sender], block.timestamp);
            commitFunds[msg.sender] = 0;
            }

            lock = false;

        }
        function complete() public {

            require(status() == Status.Successful, "Campaign not successful");
            (bool sent) = beneficiary.send(totalFunds); 
            if (sent) {
                emit Withdrawal(beneficiary, totalFunds, block.timestamp);
            }

        }
    }
    ''',
        output_values=['abi', 'bin'],
        solc_version='0.8.9'
    )

    w3 = Web3(Web3.HTTPProvider("https://rpc.apothem.network"))
    contract_id, contract_interface = compiled_sol.popitem()
    bytecode = contract_interface['bin']
    abi = contract_interface['abi']
    w3.middleware_onion.add(construct_sign_and_send_raw_middleware(w3.eth.account.from_key('0xc9623d98f3f3515c1e4e1cab7d903504a12b8df210cb850327eb0ec8e0f4a630')))
    w3.eth.default_account = '0x148E174151CcAd45F26b320f0D2656417B6CE126'
    Greeter = w3.eth.contract(abi=abi, bytecode=bytecode)
    tx_hash = Greeter.constructor(100, 100).transact({
            'gas': 5000000,
            'value': 0,
            'gasPrice': w3.eth.gas_price
    })
    try:
        tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    except Exception as error:
        address = str(error).split('\'')[1].split('\'')[0]
        Greeter.address = '0x' + address[3:]
    start_time = time.time()
        
    fundraiser = w3.eth.contract(
        address=w3.to_checksum_address(Greeter.address.lower()),
        abi=abi
    )
    tx_hash = w3.to_hex(tx_hash)
    await context.bot.send_message(chat_id=context.job.chat_id, text=f'Fundraiser deployed at: <code>{Greeter.address}</code> \nTx Hash: <code>{tx_hash}</code>', parse_mode='html')
    await asyncio.sleep(0.05)
    pickle.dump({'address': w3.to_checksum_address(Greeter.address.lower()), 'abi': abi, 'ending_time': int(start_time) + int(ending_time), 'fundraise_amount': fundraise_amount}, open(f'contracts/fundraiser{context.job.chat_id}.pkl', 'wb'))
    return

def deposit(priv_key, abi, amount, address):
    w3 = Web3(Web3.HTTPProvider("https://rpc.apothem.network"))
    w3.eth.set_gas_price_strategy(rpc_gas_price_strategy)
    acc = w3.eth.account.from_key(priv_key)
    w3.middleware_onion.inject(geth_poa_middleware, layer=0)
    w3.middleware_onion.add(construct_sign_and_send_raw_middleware(acc))
    fundraiser = w3.eth.contract(
        address=w3.to_checksum_address(address.lower()),
        abi=abi
    )

    tx_hash = fundraiser.functions.deposit().transact({
            'gas': 500000,
            'value': int(amount)*10**18,
            'gasLimit': 10000000000,
    })
    print('IT WENT THROU??')
    #signed_tx = w3.eth.account.sign_transaction(deposit_function, priv_key)
    #tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
    #print(signed_tx.rawTransaction)
    #print(tx_hash)
    return w3.to_hex(tx_hash)
    
    