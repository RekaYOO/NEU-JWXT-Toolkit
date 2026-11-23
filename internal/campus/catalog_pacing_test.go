package campus

import (
	"context"
	"errors"
	"testing"
	"time"
)

func TestCatalogPacingCancellationPreservesReservation(t *testing.T) {
	client := &Client{}
	before := time.Now()
	if err := client.waitCatalogRequest(context.Background()); err != nil {
		t.Fatal(err)
	}
	if client.nextCatalogRequest.Sub(before) < 550*time.Millisecond {
		t.Fatal("catalog requests are not paced")
	}
	reserved := client.nextCatalogRequest
	ctx, cancel := context.WithTimeout(context.Background(), time.Millisecond)
	defer cancel()
	if err := client.waitCatalogRequest(ctx); !errors.Is(err, context.DeadlineExceeded) || !client.nextCatalogRequest.Equal(reserved) {
		t.Fatal(err, client.nextCatalogRequest)
	}
}
