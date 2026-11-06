User documentation
==================

Installation, configuration, public APIs and troubleshooting.

.. mdinclude:: ../README.md

.. toctree::
   :maxdepth: 1
   :caption: User guide and reference

   client-input-policy
   configuration-validation
   textual
   release-2.0.0rc1
   release-1.3.7
   release-1.3.6
   release-1.3.5
   release-1.3.4
   release-1.3.3
   release-1.3.2
   release-1.3.1
   release-1.3.0
   release-1.2.0
   release-1.2.1
   release-1.0


Contributor documentation
-------------------------

Changing the project? Use the separate :doc:`contributors` guide.

.. toctree::
   :maxdepth: 1
   :caption: For contributors

   contributors

### Slow HTTP clients

The daemon allows ten seconds per request to receive the request line and all HTTP headers, including over native mTLS after the handshake. Sending individual bytes does not restart the deadline. On expiry the server closes the connection and the worker can accept another request. Request bodies and job execution are outside this header deadline.

HTTP request bodies have a separate absolute read deadline. The budget is ten seconds plus one second per 32 KiB of declared body size, up to two minutes. For example, a 1 MiB body has 42 seconds. A timeout returns HTTP 408 and closes the connection before the endpoint runs. Existing body size limits still apply. The deadline ends when the body is read and does not limit job execution.
