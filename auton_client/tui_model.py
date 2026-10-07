"""Presentation helpers shared by optional terminal renderers."""
STATUS_LABELS = {'new': 'queued', 'processing': 'running', 'complete': 'completed'}


def status_label(job):
    if job.get('execution_uncertain'):
        return 'unknown'
    if job.get('outcome') == 'job.cancelled':
        return 'cancelled'
    if job.get('cancel_requested') and job['status'] != 'complete':
        return 'cancelling'
    if job['status'] == 'complete' and job.get('return_code') not in (0, None):
        return 'failed'
    return STATUS_LABELS[job['status']]


def daemon_specs(specs, uris, configured=None):
    from auton_client.connections import select_connections
    if specs and uris:
        raise ValueError('use --daemon or a single --uri for TUI, not both')
    if not specs:
        if len(uris) != 1:
            raise ValueError('TUI needs --daemon NAME=URI or one --uri; failover URIs are not targets')
        return {'default': uris[0]}
    return select_connections(specs, configured=configured)

