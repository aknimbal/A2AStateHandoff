# A2AStateHandoff

Python Azure Functions sample for validating an agent-to-agent state handoff workflow before adding more agents.

The orchestrator connects four small agents through an Agent Framework-style handoff runtime:

1. `intake` captures the buyer request into shared state.
2. `inventory` validates catalog availability.
3. `quote` creates a quote and stops at the buyer confirmation gate.
4. `confirmation` persists an order only after `buyer_confirmed` is true.

The shared state includes the current status, next agent, handoff history, normalized request, inventory details, quote, and any completed orders. State is persisted to JSON using `STATE_STORE_PATH` so separate requests can resume the same session.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run tests

```bash
python -m unittest
```

## Try the orchestrator locally

Start an order. The workflow stops at the buyer confirmation gate:

```bash
STATE_STORE_PATH=/tmp/a2a-state.json \
python -m handoff.orchestrator "buy 2 widgets" --session-id demo
```

Confirm the persisted session:

```bash
STATE_STORE_PATH=/tmp/a2a-state.json \
python -m handoff.orchestrator --session-id demo --confirm
```

## Run as an Azure Function locally

1. Install [Azure Functions Core Tools](https://learn.microsoft.com/azure/azure-functions/functions-run-local).
2. Create `local.settings.json` with local state storage:

   ```json
   {
     "IsEncrypted": false,
     "Values": {
       "AzureWebJobsStorage": "UseDevelopmentStorage=true",
       "FUNCTIONS_WORKER_RUNTIME": "python",
       "STATE_STORE_PATH": "/tmp/a2a-state.json"
     }
   }
   ```

3. Start Azurite or use a real storage account for `AzureWebJobsStorage`.
4. Start the function host:

   ```bash
   func start
   ```

5. Start a session:

   ```bash
   curl -X POST http://localhost:7071/api/orchestrate \
     -H "Content-Type: application/json" \
     -d '{"session_id":"demo","message":"buy 2 widgets"}'
   ```

6. Confirm the buyer gate:

   ```bash
   curl -X POST http://localhost:7071/api/orchestrate \
     -H "Content-Type: application/json" \
     -d '{"session_id":"demo","buyer_confirmed":true}'
   ```

## Deploy to Azure Functions

1. Log in and choose a subscription:

   ```bash
   az login
   az account set --subscription "<subscription-id>"
   ```

2. Create a resource group:

   ```bash
   az group create --name rg-a2a-state-handoff --location eastus
   ```

3. Create a storage account:

   ```bash
   az storage account create \
     --name <globally-unique-storage-name> \
     --resource-group rg-a2a-state-handoff \
     --location eastus \
     --sku Standard_LRS
   ```

4. Create a Linux Python Function App:

   ```bash
   az functionapp create \
     --resource-group rg-a2a-state-handoff \
     --consumption-plan-location eastus \
     --runtime python \
     --runtime-version 3.11 \
     --functions-version 4 \
     --name <globally-unique-function-name> \
     --storage-account <globally-unique-storage-name> \
     --os-type Linux
   ```

5. Configure the state file path. For production, replace this JSON file with durable storage such as Azure Blob Storage, Table Storage, or Cosmos DB.

   ```bash
   az functionapp config appsettings set \
     --resource-group rg-a2a-state-handoff \
     --name <globally-unique-function-name> \
     --settings STATE_STORE_PATH=/tmp/a2a-state.json
   ```

6. Deploy from the repository root:

   ```bash
   func azure functionapp publish <globally-unique-function-name>
   ```

7. Call the deployed endpoint:

   ```bash
   curl -X POST "https://<globally-unique-function-name>.azurewebsites.net/api/orchestrate?code=<function-key>" \
     -H "Content-Type: application/json" \
     -d '{"session_id":"demo","message":"buy 2 widgets"}'
   ```

8. Confirm the same session:

   ```bash
   curl -X POST "https://<globally-unique-function-name>.azurewebsites.net/api/orchestrate?code=<function-key>" \
     -H "Content-Type: application/json" \
     -d '{"session_id":"demo","buyer_confirmed":true}'
   ```
