# Auton 1.3.5

The daemon now bounds the time it waits for an incomplete HTTP request body. Previously, clients could occupy every HTTP worker after sending valid request headers.

The body budget is ten seconds plus one second per 32 KiB of declared body size, capped at two minutes. A 256 KiB body gets 18 seconds and a 1 MiB body gets 42 seconds. Progress does not reset the deadline. The same rule applies over native mTLS. On timeout the daemon returns HTTP 408 and closes the connection before dispatching the job.

## Upgrade from 1.3.4

Update the daemon or deploy the versioned Docker image and restart the service. Preserve credentials, configuration and persistent volumes. Clients uploading large files over slow links should check their transfer time against the body budget. Job execution timeouts are unchanged.

```sh
python -m pip install --upgrade 'autond[auth]==1.3.5'
python -m pip install --upgrade 'auton==1.3.5'
docker pull decryptus/auton:1.3.5
```
