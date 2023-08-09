#!/bin/bash

set -e  # Exit script on first error

# Check dependencies
command -v git >/dev/null 2>&1 ||
{ echo >&2 "Git is not installed!";
  exit 1
}

command -v poetry >/dev/null 2>&1 ||
{ echo >&2 "Poetry is not installed!";
  exit 1
}

# Prompt for RPC
[[ -z "${RPC_0}" ]] && read -rsp "Enter a Gnosis RPC that supports eth_newFilter [hidden input]: " RPC_0 || RPC_0="${RPC_0}"
echo

# Check if eth_newFilter is supported
new_filter_supported=$(curl -s -S -X POST \
  -H "Content-Type: application/json" \
  --data '{"jsonrpc":"2.0","method":"eth_newFilter","params":["invalid"],"id":1}' "$RPC_0" | \
  python3 -c "import sys, json; print(json.load(sys.stdin)['error']['message']=='The method eth_newFilter does not exist/is not available')")

if [ "$new_filter_supported" = True ]
then
    echo "The given RPC ($RPC_0) does not support 'eth_newFilter'! Terminating script..."
    exit 1
fi

# clone repo
directory="trader"
# This is a tested version that works well.
# Feel free to replace this with a different version of the repo, but be careful as there might be breaking changes
service_version="v0.3.0"
service_repo=https://github.com/valory-xyz/$directory.git
if [ -d $directory ]
then
    echo "Detected an existing $directory repo. Using this one..."
else
    echo "Cloning the $directory repo..."
    git clone --depth 1 --branch $service_version $service_repo
fi

cd $directory
if [ "$(git rev-parse --is-inside-work-tree)" = true ]
then
    poetry install
else
    echo "$directory is not a git repo!"
    exit 1
fi

# Generate the operator's key
keys_json="keys.json"
address_start_position=17
pkey_start_position=21
operator_pkey_file="operator_pkey.txt"
poetry run autonomy generate-key -n1 ethereum
operator_address=$(sed -n 3p $keys_json)
operator_address=$(echo "$operator_address" | \
  awk '{ print substr( $0, '$address_start_position', length($0) - '$address_start_position' - 1 ) }')
printf "Your operator's autogenerated public address: %s
The same address will be used as the owner.\n" "$operator_address"
operator_pkey=$(sed -n 4p $keys_json)
echo -n "$operator_pkey" | awk '{ printf substr( $0, '$pkey_start_position', length($0) - '$pkey_start_position' ) }' > $operator_pkey_file
mv $keys_json operator_keys.json

# Generate the agent's key
poetry run autonomy generate-key -n1 ethereum
agent_address=$(sed -n 3p $keys_json)
agent_address=$(echo "$agent_address" | \
  awk '{ print substr( $0, '$address_start_position', length($0) - '$address_start_position' - 1 ) }')
private_key=$(sed -n 4p $keys_json)
private_key=$(echo "$private_key" | \
  awk '{ print substr( $0, '$pkey_start_position', length($0) - '$pkey_start_position' ) }')
echo "Your agent's autogenerated public address: $agent_address"

# Check balances
agent_balance=$(curl -s -S -X POST \
  -H "Content-Type: application/json" \
  --data '{"jsonrpc":"2.0","method":"eth_getBalance","params":["'"$agent_address"'","latest"],"id":1}' "$RPC_0" | \
  python3 -c "import sys, json; print(json.load(sys.stdin)['result'])")
operator_balance=$(curl -s -S -X POST \
  -H "Content-Type: application/json" \
  --data '{"jsonrpc":"2.0","method":"eth_getBalance","params":["'"$operator_address"'","latest"],"id":1}' "$RPC_0" | \
  python3 -c "import sys, json; print(json.load(sys.stdin)['result'])")
agent_balance=$((16#${agent_balance#??}))
operator_balance=$((16#${operator_balance#??}))
suggested_amount=500000000000000000
until [[ $agent_balance -gt $suggested_amount-1 && $operator_balance -gt $suggested_amount-1 ]]
do
    echo "Agent's balance: $agent_balance WEI."
    echo "Operator's balance: $operator_balance WEI."
    echo "Both of the addresses need to be funded."
    echo "Please fund them with at least 0.5 xDAI each to continue."
    echo "Checking again in 10s..."
    sleep 10s
done

echo "Minting your service..."

# setup the minting tool
export CUSTOM_CHAIN_RPC=$RPC_0
export CUSTOM_CHAIN_ID=100
export CUSTOM_SERVICE_MANAGER_ADDRESS="0xE3607b00E75f6405248323A9417ff6b39B244b50"
export CUSTOM_SERVICE_REGISTRY_ADDRESS="0x9338b5153AE39BB89f50468E608eD9d764B755fD"
export CUSTOM_GNOSIS_SAFE_MULTISIG_ADDRESS="0x3C1fF68f5aa342D296d4DEe4Bb1cACCA912D95fE"

# create service
agent_id=12
nft="bafybeig64atqaladigoc3ds4arltdu63wkdrk3gesjfvnfdmz35amv7faq"
service_id=$(poetry run autonomy mint \
  --skip-hash-check \
  --use-custom-chain \
  service packages/valory/services/$directory/ \
  --key "$operator_pkey_file" \
  --nft $nft \
  -a $agent_id \
  -n 1 \
  --threshold 1 \
  -c 10000000000000000
  )
# parse only the id from the response
service_id="${service_id##*: }"
# validate id
if ! [[ "$service_id" =~ ^[0-9]+$ || "$service_id" =~ ^[-][0-9]+$ ]]
then
    echo "Service minting failed: $service_id"
    exit 1
fi

# activate service
poetry run autonomy service --use-custom-chain activate --key "$operator_pkey_file" "$service_id"
# register service
poetry run autonomy service --use-custom-chain register --key "$operator_pkey_file" "$service_id" -a $agent_id -i "$agent_address"
# deploy service
poetry run autonomy service --use-custom-chain deploy --key "$operator_pkey_file" "$service_id"

# delete the operator's pkey file
rm $operator_pkey_file

# check state
expected_state="| Service State             | DEPLOYED                                     |"
service_info=$(poetry run autonomy service --use-custom-chain info "$service_id")
service_state=$(echo "$service_info" | grep "Service State")
if [ "$service_state" != "$expected_state" ]
then
    echo "Something went wrong while deploying the service. The service's state is:"
    echo "$service_state"
    echo "Please check the output of the script for more information."
    exit 1
fi

# get the deployed service's safe address from the contract
safe=$(echo "$service_info" | grep "Multisig Address")
address_start_position=22
safe=$(echo "$safe" |
  awk '{ print substr( $0, '$address_start_position', length($0) - '$address_start_position' - 1 ) }')
export SAFE_CONTRACT_ADDRESS=$safe

# Check the safe's balance
safe_balance=$(curl -s -S -X POST \
  -H "Content-Type: application/json" \
  --data '{"jsonrpc":"2.0","method":"eth_getBalance","params":["'"$SAFE_CONTRACT_ADDRESS"'","latest"],"id":1}' "$RPC_0" | \
  python3 -c "import sys, json; print(json.load(sys.stdin)['result'])")
safe_balance=$((16#${safe_balance#??}))
until [[ $safe_balance -gt 0 ]]
do
    echo "Safe's balance: $safe_balance WEI."
    echo "The safe address needs to be funded. Please fund it to continue. Retrying in 10s..."
    sleep 10s
done

# Set environment variables. Tweak these to modify your strategy
export RPC_0="$RPC_0"
export CHAIN_ID=$CUSTOM_CHAIN_ID
export ALL_PARTICIPANTS='["'$agent_address'"]'
# This is the default market creator. Feel free to update with other market creators
export OMEN_CREATORS='["0x89c5cc945dd550BcFfb72Fe42BfF002429F46Fec"]'
export BET_AMOUNT_PER_THRESHOLD_000=0
export BET_AMOUNT_PER_THRESHOLD_010=0
export BET_AMOUNT_PER_THRESHOLD_020=0
export BET_AMOUNT_PER_THRESHOLD_030=0
export BET_AMOUNT_PER_THRESHOLD_040=0
export BET_AMOUNT_PER_THRESHOLD_050=0
export BET_AMOUNT_PER_THRESHOLD_060=30000000000000000
export BET_AMOUNT_PER_THRESHOLD_070=40000000000000000
export BET_AMOUNT_PER_THRESHOLD_080=60000000000000000
export BET_AMOUNT_PER_THRESHOLD_090=80000000000000000
export BET_AMOUNT_PER_THRESHOLD_100=100000000000000000
export BET_THRESHOLD=5000000000000000
export PROMPT_TEMPLATE="With the given question \"@{question}\" and the \`yes\` option represented by \`@{yes}\`
and the \`no\` option represented by \`@{no}\`,
what are the respective probabilities of \`p_yes\` and \`p_no\` occurring?"

service_dir="trader_service"
build_dir="abci_build"
directory="$service_dir/$build_dir"
if [ -d $directory ]
then
    echo "Detected an existing build. Using this one..."
    cd $service_dir
    sudo rm -rf $build_dir
else
    echo "Setting up the service..."

    if ! [ -d "$service_dir" ]; then
        # Fetch the service
        poetry run autonomy fetch --service valory/trader:$service_version --alias $service_dir
    fi

    cd $service_dir
    # Build the image
    poetry run autonomy build-image
    mv ../../$keys_json $keys_json
fi

# Build the deployment with a single agent
poetry run autonomy deploy build --n 1 -ltm
# Run the deployment
poetry run autonomy deploy run --build-dir $build_dir
