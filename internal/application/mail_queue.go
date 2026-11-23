package application

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/notifications"
	"github.com/RekaYOO/NEU-JWXT-Toolkit/internal/store"
)

func (api *API) queueMail(ctx context.Context, session store.Session, source, dedupe string, message notifications.MailMessage) (bool, error) {
	if !message.Valid() {
		return false, errors.New("invalid mail message")
	}
	config, err := api.readMailConfig(ctx, session)
	if err != nil {
		return false, err
	}
	if !config.Valid(true) {
		return false, nil
	}
	message.ID = ""
	payload, err := json.Marshal(message)
	if err != nil {
		return false, err
	}
	queued, err := api.database.QueueMail(ctx, session, source, dedupe, payload)
	if err == nil && queued {
		select {
		case api.accounts.mailWake <- struct{}{}:
		default:
		}
	}
	return queued, err
}

func (api *API) startMailSender() {
	api.accounts.mutex.Lock()
	defer api.accounts.mutex.Unlock()
	if api.accounts.context.Err() != nil {
		return
	}
	api.accounts.mailTick = api.scheduleMail
	api.accounts.mailOnce.Do(func() {
		api.accounts.mailWake = make(chan struct{}, 1)
		api.accounts.mailActive = map[string]bool{}
		api.accounts.mailSlots = make(chan struct{}, 2)
		api.accounts.workers.Add(1)
		go func() {
			defer api.accounts.workers.Done()
			ticker := time.NewTicker(20 * time.Second)
			defer ticker.Stop()
			for {
				select {
				case <-api.accounts.context.Done():
					return
				case <-ticker.C:
				case <-api.accounts.mailWake:
				}
				api.accounts.mutex.RLock()
				callback := api.accounts.mailTick
				api.accounts.mutex.RUnlock()
				if callback != nil {
					callback()
				}
			}
		}()
	})
}

func (api *API) scheduleMail() {
	sessions, err := api.database.DueMailAccounts(api.accounts.context, time.Now())
	if err != nil {
		return
	}
	for _, session := range sessions {
		api.accounts.mutex.Lock()
		if api.accounts.context.Err() != nil || api.accounts.mailActive[session.Account] {
			api.accounts.mutex.Unlock()
			continue
		}
		select {
		case api.accounts.mailSlots <- struct{}{}:
		default:
			api.accounts.mutex.Unlock()
			return
		}
		api.accounts.mailActive[session.Account] = true
		api.accounts.workers.Add(1)
		runtime := api.accounts.runtimes[session.Account]
		api.accounts.mutex.Unlock()
		go func() {
			defer api.accounts.workers.Done()
			defer func() {
				api.accounts.mutex.Lock()
				delete(api.accounts.mailActive, session.Account)
				<-api.accounts.mailSlots
				api.accounts.mutex.Unlock()
				select {
				case api.accounts.mailWake <- struct{}{}:
				default:
				}
			}()
			ctx, cancel := context.WithCancel(api.accounts.context)
			defer cancel()
			if runtime != nil && runtime.Generation == session.Generation {
				stop := context.AfterFunc(runtime.Context, cancel)
				defer stop()
			}
			api.deliverOneMail(ctx, session)
		}()
	}
}

func (api *API) deliverOneMail(ctx context.Context, session store.Session) error {
	entry, err := api.database.ClaimMail(ctx, session, time.Now())
	if err != nil {
		return err
	}
	config, err := api.readMailConfig(ctx, session)
	var message notifications.MailMessage
	if err == nil && (!config.Valid(true) || json.Unmarshal(entry.Payload, &message) != nil) {
		err = errors.New("mail configuration or payload unavailable")
	}
	if err == nil {
		valid, checkError := api.mailEntryValid(ctx, entry, message)
		if checkError != nil {
			err = checkError
		} else if !valid {
			return api.database.DiscardMail(ctx, session, entry.Source, entry.Dedupe)
		}
	}
	if err == nil {
		err = api.renderRecoveryMail(ctx, entry, &message)
		if errors.Is(err, store.ErrMissing) {
			return api.database.DiscardMail(ctx, session, entry.Source, entry.Dedupe)
		}
	}
	if err == nil {
		message.ID = entry.ID
		err = api.sendMail(ctx, config, message)
	}
	finishContext, cancel := context.WithTimeout(context.WithoutCancel(ctx), 3*time.Second)
	defer cancel()
	errorKind := ""
	if err != nil {
		errorKind = "delivery_failed"
	}
	if finishErr := api.database.FinishMail(finishContext, entry, err == nil, errorKind, time.Now()); finishErr != nil {
		return finishErr
	}
	return err
}
