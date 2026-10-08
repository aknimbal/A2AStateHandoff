# A2AStateHandoff

Python Azure Functions sample for validating an agent-to-agent state handoff workflow before adding more agents.

The orchestrator connects four small agents through an Agent Framework-style handoff runtime:

1. `intake` captures the buyer request into shared state.
2. `inventory` validates catalog availability.
3. `quote` creates a quote and stops at the buyer confirmation gate.
4. `confirmation` persists an order only after `buyer_confirmed` is true.

The shared state includes the current status, next agent, handoff history, normalized request, inventory details, quote, any completed orders, and the authenticated caller's claims. State is persisted to JSON using `STATE_STORE_PATH` so separate requests can resume the same session.

## Authentication and agent-to-agent pass-through

Callers authenticate with a shared-secret bearer token. Every value — header name, scheme, client ids, tokens, scopes, agent scope requirements, and the handoff signing key — comes from the environment (seeded by `.env`); nothing is hardcoded.

1. **Caller authentication.** `handoff.auth.authenticate_request` reads the credential from the configured header (`AUTH_HEADER_NAME` / `AUTH_SCHEME`) and matches it in constant time against the tokens of the clients in `AUTH_CLIENT_IDS`. A match produces a `Principal` with that client's scopes. Unknown or missing credentials return `401`.
2. **Per-agent authorization.** Each agent declares a required scope through `AGENT_SCOPE_<AGENT>`. Before an agent runs, the orchestrator checks the principal's scopes; a mismatch stops the chain with `stop_reason=insufficient_scope` and the HTTP endpoint returns `403`.
3. **Pass-through between agents.** On every handoff the orchestrator mints an HMAC-signed token (`HANDOFF_SIGNING_SECRET`) binding the session id, source agent, target agent, client id, credential fingerprint, and scopes. The next agent only runs if that token verifies, and it receives the caller identity as `context.principal` plus the hop credential as `context.handoff_token`. Every hop is appended to `auth.delegation_chain` in shared state for auditing. A tampered or replayed token stops the chain with `stop_reason=invalid_handoff_token`.
4. **Session binding.** With `AUTH_SESSION_BINDING_ENABLED=true`, a session can only be resumed by the client that created it (`403` otherwise).
5. Set `AUTH_ENABLED=false` to run the chain without credentials for local experimentation.

Raw tokens are never written to state — only a fingerprint and signed hop tokens are persisted.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env and replace every placeholder secret
```

`.env` is gitignored. Azure Functions app settings (or any real environment variable) always win over `.env`, so the same code runs locally and in the cloud.

## Run tests

```bash
python -m unittest
```

## Try the orchestrator locally

The CLI authenticates with `--token`, falling back to `CLIENT_TOKEN` from `.env`.

Start an order. The workflow stops at the buyer confirmation gate:

```bash
python -m handoff.orchestrator "buy 2 widgets" --session-id demo
```

Confirm the persisted session:

```bash
python -m handoff.orchestrator --session-id demo --confirm
```

Run as a client whose scopes stop the chain early:

```bash
python -m handoff.orchestrator "buy 1 widget" --session-id scoped --token "$INVENTORY_BOT_TOKEN"
# Client 'inventory-bot' lacks scope 'quote.read' required by agent 'quote'.
```

## Run as an Azure Function locally

1. Install [Azure Functions Core Tools](https://learn.microsoft.com/azure/azure-functions/functions-run-local).
2. Create `local.settings.json`. Application configuration stays in `.env`, which the worker loads on startup:

   ```json
   {
     "IsEncrypted": false,
     "Values": {
       "AzureWebJobsStorage": "UseDevelopmentStorage=true",
       "FUNCTIONS_WORKER_RUNTIME": "python"
     }
   }
   ```

3. Start Azurite or use a real storage account for `AzureWebJobsStorage`.
4. Start the function host:

   ```bash
   func start
   ```

5. Start a session (the bearer token must match a client configured in `.env`):

   ```bash
   curl -X POST http://localhost:7071/api/orchestrate \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer $CLIENT_TOKEN" \
     -d '{"session_id":"demo","message":"buy 2 widgets"}'
   ```

6. Confirm the buyer gate:

   ```bash
   curl -X POST http://localhost:7071/api/orchestrate \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer $CLIENT_TOKEN" \
     -d '{"session_id":"demo","buyer_confirmed":true}'
   ```

   Responses include the authenticated client, its scopes, and the `delegation_chain` of signed agent-to-agent hops. Missing or invalid credentials return `401`; scope or handoff-token failures return `403`.

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

5. Push the configuration from `.env` as app settings. Keep secrets out of source control — reference Key Vault for the token and signing values in production. For production, also replace the JSON state file with durable storage such as Azure Blob Storage, Table Storage, or Cosmos DB.

   ```bash
   az functionapp config appsettings set \
     --resource-group rg-a2a-state-handoff \
     --name <globally-unique-function-name> \
     --settings $(grep -v '^#' .env | grep -v '^$' | xargs)
   ```

6. Deploy from the repository root:

   ```bash
   func azure functionapp publish <globally-unique-function-name>
   ```

7. Call the deployed endpoint:

   ```bash
   curl -X POST "https://<globally-unique-function-name>.azurewebsites.net/api/orchestrate?code=<function-key>" \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer $CLIENT_TOKEN" \
     -d '{"session_id":"demo","message":"buy 2 widgets"}'
   ```

8. Confirm the same session:

   ```bash
   curl -X POST "https://<globally-unique-function-name>.azurewebsites.net/api/orchestrate?code=<function-key>" \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer $CLIENT_TOKEN" \
     -d '{"session_id":"demo","buyer_confirmed":true}'
   ```
