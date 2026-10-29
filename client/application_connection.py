"""Private stdin receiver; stdout contains only bound acknowledgements."""

import hashlib
import json
import signal
import sys

try:
    from .eyre import Eyre
    from .transport_journal import TransportJournal
    from .instance import INSTANCE
    from .connection_offer import HandoffError, acknowledgement, check_identity, decode, plugin_session
    from .connection_journal import Journal, fingerprint, reconcile_request, reconciled, secret_present
    from .support import Keyring, StateStore, empty_ship
except ImportError:
    from eyre import Eyre
    from transport_journal import TransportJournal
    from instance import INSTANCE
    from connection_offer import HandoffError, acknowledgement, check_identity, decode, plugin_session
    from connection_journal import Journal, fingerprint, reconcile_request, reconciled, secret_present
    from support import Keyring, StateStore, empty_ship


def account_id(value):
    return hashlib.sha256((value['connection_id'] + ':' + value['request_id']).encode()).hexdigest()


def accept(raw, store=None, keyring=None, instance=INSTANCE, verify=check_identity):
    offer = decode(raw, instance)
    store = store if store is not None else StateStore(instance=instance)
    keyring = keyring if keyring is not None else Keyring(instance=instance)
    session = offer['session']
    with store.locked(wait=True):
        record = store.load()
        if any(row['url'] == session['origin'] for row in record['ships']):
            raise HandoffError('account-exists')
        if len(record['ships']) >= 64:
            raise HandoffError('account-limit')
        verify(session)
        journal = Journal(store.root)
        pending = journal.begin(offer, plugin_session(session))
        keyring.store(session['origin'], plugin_session(session))
        # Authentication does not authorize publishing or desktop theme sync.
        record['ships'].append(dict(empty_ship(), id=account_id(offer), url=session['origin'],
                                    ship=session['ship'], fingerprint=''))
        store.save(record)
        pending['phase'] = 'accepted'
        journal.save()
    return acknowledgement(offer)


def reconcile(raw, store=None, keyring=None, instance=INSTANCE, present=secret_present):
    value = reconcile_request(raw, instance)
    store = store if store is not None else StateStore(instance=instance)
    keyring = keyring if keyring is not None else Keyring(instance=instance)
    with store.locked(wait=True):
        journal = Journal(store.root)
        row = journal.find(value)
        if row is None:
            return reconciled(value, 'absent')
        if row['phase'] == 'reconciled':
            return reconciled(value, row['outcome'])
        record = store.load()
        current = next((item for item in record['ships'] if item['url'] == row['origin']), None)
        if current is not None and (current['id'] != account_id(value) or current['ship'] != row['ship']):
            journal.finish(row, 'superseded')
            return reconciled(value, 'superseded')
        session = None
        if present(instance, row['origin']):
            session = keyring.lookup(row['origin'], row['ship'], check_expiry=False)
            if fingerprint(session) != row['fingerprint']:
                journal.finish(row, 'superseded')
                return reconciled(value, 'superseded')
        if current is not None:
            current.update(automatic=False, pending=False)
            store.save(record)
        transport = Eyre(row['origin'], session, journal=TransportJournal(store.root, instance))
        with transport.disconnecting(timeout=15):
            if session is not None:
                keyring.clear(row['origin'])
                if present(instance, row['origin']):
                    raise HandoffError('keyring-unavailable')
            outcome = 'absent'
            if current is not None and current['id'] == account_id(value) and current['ship'] == row['ship']:
                record['ships'].remove(current)
                store.save(record)
                outcome = 'removed'
            elif current is not None:
                outcome = 'superseded'
        journal.finish(row, outcome)
        return reconciled(value, outcome)


def main():
    def timeout(*_args):
        raise HandoffError('timeout')
    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(25)
    try:
        raw = sys.stdin.buffer.read(16385)
        if sys.argv[1:] == ['--reconcile']:
            result = reconcile(raw)
        elif not sys.argv[1:]:
            result = accept(raw)
        else:
            raise HandoffError('invalid')
    except HandoffError as error:
        result = {'version': 1, 'status': 'rejected', 'code': str(error)}
    except Exception:
        result = {'version': 1, 'status': 'rejected', 'code': 'storage'}
    finally:
        signal.alarm(0)
    print(json.dumps(result, separators=(',', ':')), flush=True)
    return 0 if result['status'] in ('accepted', 'reconciled') else 1


if __name__ == '__main__':
    sys.exit(main())
