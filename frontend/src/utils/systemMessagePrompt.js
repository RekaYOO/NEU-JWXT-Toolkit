import { relatedSystemMessages } from './systemMessageSummary';
import { markSystemMessagesRead } from '../services/api';

const acknowledgedByAccount = new Map();
const STORAGE_KEY = 'neu-system-message-acknowledged-v1';

const keyOf = (kind, item) => `${kind}:${item.kind || 'reminder'}:${item.id || item.message_id || ''}`;
const fingerprintOf = item => `${item.title || ''}|${item.content || ''}|${item.sent_at || ''}`;

const readStoredAcknowledgements = () => {
  if (typeof window === 'undefined' || !window.sessionStorage) return {};
  try {
    const value = JSON.parse(window.sessionStorage.getItem(STORAGE_KEY) || '{}');
    return value && typeof value === 'object' ? value : {};
  } catch (_error) {
    return {};
  }
};

const getAcknowledged = account => {
  let acknowledged = acknowledgedByAccount.get(account);
  if (!acknowledged) {
    acknowledged = new Map();
    const stored = readStoredAcknowledgements()[account];
    if (stored && typeof stored === 'object') {
      Object.entries(stored).forEach(([key, value]) => {
        if (typeof value === 'string') acknowledged.set(key, value);
        else if (value && typeof value.fingerprint === 'string') {
          acknowledged.set(key, value.fingerprint);
        }
      });
    }
    acknowledgedByAccount.set(account, acknowledged);
  }
  return acknowledged;
};

const persistAcknowledgements = (account, acknowledged) => {
  if (typeof window === 'undefined' || !window.sessionStorage) return;
  try {
    const stored = readStoredAcknowledgements();
    stored[account] = Object.fromEntries(
      [...acknowledged.entries()].slice(-200).map(([key, fingerprint]) => [
        key,
        { fingerprint, acknowledgedAt: Date.now() },
      ]),
    );
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(stored));
  } catch (_error) {
    // Storage can be unavailable in private browsing or embedded webviews.
  }
};

export const unreadSystemMessagePrompts = (messages, kind, account) => {
  if (!account) return [];
  const acknowledged = getAcknowledged(account);
  return relatedSystemMessages((messages || []).filter(item => item.read === false), kind).filter(item => (
    Boolean(item.id || item.message_id)
    && acknowledged?.get(keyOf(kind, item)) !== fingerprintOf(item)
  ));
};

export const acknowledgeSystemMessagePrompts = (account, kind, items) => {
  if (!account) return;
  const acknowledged = getAcknowledged(account);
  items.forEach(item => acknowledged.set(keyOf(kind, item), fingerprintOf(item)));
  persistAcknowledgements(account, acknowledged);
};

export const acknowledgeAndSyncSystemMessagePrompts = async (account, kind, items) => {
  acknowledgeSystemMessagePrompts(account, kind, items);
  const payload = (items || []).filter(item => item?.id || item?.message_id);
  if (!payload.length || typeof markSystemMessagesRead !== 'function') return null;
  try {
    return await markSystemMessagesRead(payload);
  } catch (error) {
    // The local acknowledgement still prevents duplicate prompts this session.
    // The next official cache refresh will reconcile the remote read state.
    return { success: false, error };
  }
};

export const clearSystemMessagePromptMemory = () => {
  acknowledgedByAccount.clear();
  if (typeof window !== 'undefined' && window.sessionStorage) {
    try {
      window.sessionStorage.removeItem(STORAGE_KEY);
    } catch (_error) {
      // Storage can be unavailable in private browsing or embedded webviews.
    }
  }
};
