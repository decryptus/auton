## auton project

auton is a free and open-source, we develop it to run programs and command-lines on remote servers through HTTP protocol.
There are two programs, auton for client side and autond for server side.
auton is just a helper to transform command-lines into HTTP protocol, it is able to transform basic arguments, file arguments and environment variables.
For example, you can use auton from CI/CD to run on remote servers, you just need to configure your endpoints:
  - [ansible](https://github.com/ansible/ansible)
  - [curl](https://github.com/curl/curl)
  - [terraform](https://github.com/hashicorp/terraform)

## Quickstart

Using autond in Docker

`docker-compose up -d`

See [docker-compose.yml](docker-compose.yml)

## Environment variables

### autond

| Variable         | Description                 | Default |
|:-----------------|:----------------------------|:--------|
| `AUTOND_CONFIG`  | Configuration file contents<br />(e.g. `export AUTOND_CONFIG="$(cat auton.yml)"`) |  |
| `AUTOND_LOGFILE` | Log file path               | /var/log/autond/daemon.log |
| `AUTOND_PIDFILE` | autond pid file path        | /run/auton/autond.pid |
| `AUTON_GROUP`    | auton group                 | auton or root |
| `AUTON_USER`     | auton user                  | auton or root |

### auton

| Variable               | Description                 | Default |
|:-----------------------|:----------------------------|:--------|
| `AUTON_AUTH_USER`      | user for authentication     |  |
| `AUTON_AUTH_PASSWD`    | password for authentication |  |
| `AUTON_ENDPOINT`       | name of endpoint            |  |
| `AUTON_LOGFILE`        | Log file path               | /var/log/auton/auton.log |
| `AUTON_NO_RETURN_CODE` | Do not exit with return code if present | False |
| `AUTON_UID`            | auton job uid               | random uuid |
| `AUTON_URI`            | autond URI(s)<br />(e.g. http://auton-01.example.org:8666,http://auton-02.example.org:8666) |  |

## Autond configuration

See configuration example [etc/auton/auton.yml.example](etc/auton/auton.yml.example)

### Endpoints

In this example, we declared three endpoints: ansible-playbook-ssh, ansible-playbook-http, curl.
They used subproc plugin.

```yaml
endpoints:
  ansible-playbook-ssh:
    plugin: subproc
    config:
      prog: ansible-playbook
      timeout: 3600
      args:
        - '/etc/ansible/playbooks/ssh-install.yml'
        - '--tags'
        - 'sshd'
      become:
        enabled: true
      env:
        DISPLAY_SKIPPED_HOSTS: 'false'
  ansible-playbook-http:
    plugin: subproc
    config:
      prog: ansible-playbook
      timeout: 3600
      args:
        - '/etc/ansible/playbooks/http-install.yml'
        - '--tags'
        - 'httpd'
      become:
        enabled: true
      env:
        DISPLAY_SKIPPED_HOSTS: 'false'
  curl:
    plugin: subproc
    config:
      prog: curl
      timeout: 3600
```

### Authentication

To enable authentication, you must add `auth_basic` and `auth_basic_file` lines in section general:

```yaml
general:
  auth_basic:      'Restricted'
  auth_basic_file: '/etc/auton/auton.passwd'
```

To generate `auth_basic_file` use `htpasswd`:
`htpasswd -c -s /etc/auton/auton.passwd foo`

And you have to add for each modules route `auth: true`:

```yaml
modules:
  job:
    routes:
      run:
        handler:   'job_run'
        regexp:    '^run/(?P<endpoint>[^\/]+)/(?P<id>[a-z0-9][a-z0-9\-]{7,63})$'
        safe_init: true
        auth:      true
        op:        'POST'
      status:
        handler:   'job_status'
        regexp:    '^status/(?P<endpoint>[^\/]+)/(?P<id>[a-z0-9][a-z0-9\-]{7,63})$'
        auth:      true
        op:        'GET'
```

You can use section `users` to specify users allowed by endpoint:
```yaml
  ansible-playbook-ssh:
    plugin: subproc
    users:
      maintainer: true
      bob: true
    config:
      prog: ansible-playbook
      timeout: 3600
      args:
        - '/etc/ansible/playbooks/ssh-install.yml'
        - '--tags'
        - 'sshd'
      become:
        enabled: true
      env:
        DISPLAY_SKIPPED_HOSTS: 'false'
```

#### Plugin subproc

subproc plugin executes with subprocess

Use keyword `proc` to specify program path:
```yaml
endpoints:
  curl:
    plugin: subproc
    config:
      prog: curl
```

You can also use keyword `workdir` to change the working directory:
```yaml
endpoints:
  curl:
    plugin: subproc
    config:
      prog: curl
      workdir: somedir/
```

You can also use keyword `search_paths` to specify paths to search `prog`:
```yaml
endpoints:
  curl:
    plugin: subproc
    config:
      prog: curl
      search_paths:
        - /usr/local/bin
        - /usr/bin
        - /bin
```

You can also use section `become` to execute with an other user:
```yaml
endpoints:
  curl:
    plugin: subproc
    config:
      prog: curl
      become:
        enabled: true
        user: foo
```

You can also use keyword `timeout` to raise an exception after n seconds (default: 60 seconds):
```yaml
endpoints:
  curl:
    plugin: subproc
    config:
      prog: curl
      timeout: 3600
```

You can also use section `args` to define arguments always present:
```yaml
endpoints:
  curl:
    plugin: subproc
    config:
      prog: curl
      args:
        - '-s'
        - '-4'
```

You can also use keyword `disallow-args` to disable args from client:
```yaml
endpoints:
  curl:
    plugin: subproc
    config:
      prog: curl
      args:
        - '-vvv'
        - 'https://example.com'
      disallow-args: true
```

You can also use section `argfiles` to define arguments files always present:
```yaml
endpoints:
  curl:
    plugin: subproc
    config:
      prog: curl
      argfiles:
        - arg: '--key'
          filepath: /tmp/private_key
        - arg: '-d@'
          filepath: /tmp/data
```

You can also use keyword `disallow-argfiles` to disable arguments files from client:
```yaml
endpoints:
  curl:
    plugin: subproc
    config:
      prog: curl
      argfiles:
        - arg: '--key'
          filepath: /tmp/private_key
        - arg: '-d@'
          filepath: /tmp/data
      disallow-argfiles: true
```

You can also use section `env` to define environment variables always present:
```yaml
endpoints:
  curl:
    plugin: subproc
    config:
      prog: curl
      env:
        HTTP_PROXY: http://proxy.example.com:3128/
        HTTPS_PROXY: http://proxy.example.com:3128/
```

You can also use keyword `disallow-env` to disable environment variables from client:
```yaml
endpoints:
  curl:
    plugin: subproc
    config:
      prog: curl
      env:
        HTTP_PROXY: http://proxy.example.com:3128/
        HTTPS_PROXY: http://proxy.example.com:3128/
      disallow-env: true
```

Predefined AUTON environment variables during execution:

| Variable           | Description                                   |
|:-------------------|:----------------------------------------------|
| `AUTON`            | Mark the job is executed in AUTON environment |
| `AUTON_JOB_TIME`   | Current time in local time zone               |
| `AUTON_JOB_GMTIME` | Current time in GMT                           |
| `AUTON_JOB_UID`    | Current job uid passed from client            |
| `AUTON_JOB_UUID`   | Unique ID of the current job                  |

## Auton command-lines

#### endpoint curl examples:

Call url https://example.com:

`auton --endpoint curl --uri http://localhost:8666 -a 'https://example.com'`

You can also add environment variable HTTP\_PROXY:

`auton --endpoint curl --uri http://localhost:8666 -a 'https://example.com' -e 'HTTP_PROXY=http://proxy.example.com:3128/'`

You can also load environment variables from local files:

`auton --endpoint curl --uri http://localhost:8666 -a 'https://example.com' --load-envfile foo.env`

You can also tell to autond to load environment variables files from its local fs:

`auton --endpoint curl --uri http://localhost:8666 -a 'https://example.com' --envfile /etc/auton/auton.env`

You can also add multiple autond URIs for high availability:

`auton --endpoint curl --uri http://localhost:8666 --uri http://localhost:8667 -a 'https://example.com'`

You can also add arguments files to send local files:

`auton --endpoint curl --uri http://localhost:8666 -A '--cacert=cacert.pem' -a 'https://example.com'`

You can also add multiple arguments:

`auton --endpoint curl --uri http://localhost:8666 --multi-args '-vvv -u foo:bar https://example.com' --multi-argsfiles '-d@=foo.txt -d@=bar.txt --cacert=cacert.pem'`

You can also get file content from stdin with `-`:

`cat foo | auton --endpoint curl --uri http://localhost:8666 --multi-args '-vvv -u foo:bar sftp://example.com' --multi-argsfiles '--key=private_key.pem --pubkey=public_key.pem -T=-'`
