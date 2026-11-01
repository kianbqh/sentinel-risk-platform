// Package loaders provides per-request dataloaders that batch N+1 field lookups into one SQL query.
package loaders

import (
	"context"
	"net/http"
	"time"

	"github.com/vikstrous/dataloadgen"

	"github.com/kianbqh/sentinel-risk-platform/console/backend/graph/model"
	"github.com/kianbqh/sentinel-risk-platform/console/backend/internal/store"
)

type ctxKey struct{}

type Loaders struct {
	Events *dataloadgen.Loader[string, []*model.CaseEvent]
	Labels *dataloadgen.Loader[string, []*model.CaseLabel]
}

func Middleware(st *store.Store, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		l := &Loaders{Events: dataloadgen.NewLoader(st.EventsByCase, dataloadgen.WithWait(time.Millisecond)),
			Labels: dataloadgen.NewLoader(st.LabelsByCase, dataloadgen.WithWait(time.Millisecond))}
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), ctxKey{}, l)))
	})
}

func For(ctx context.Context) *Loaders {
	l, _ := ctx.Value(ctxKey{}).(*Loaders)
	return l
}

// Invalidate keeps serial mutation aliases consistent within this request.
func Invalidate(ctx context.Context, id string) {
	if l := For(ctx); l != nil {
		l.Events.Clear(id)
		l.Labels.Clear(id)
	}
}
