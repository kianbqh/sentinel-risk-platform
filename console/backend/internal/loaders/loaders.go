// Package loaders provides per-request dataloaders that batch N+1 field lookups into one SQL query.
package loaders

import (
	"context"
	"net/http"
	"time"

	"github.com/vikstrous/dataloadgen"

	"github.com/kianbqh/fullstack-go-graphql/backend/graph/model"
	"github.com/kianbqh/fullstack-go-graphql/backend/internal/store"
)

type ctxKey struct{}

type Loaders struct {
	Events *dataloadgen.Loader[string, []*model.StatusEvent]
}

func Middleware(st *store.Store, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		l := &Loaders{Events: dataloadgen.NewLoader(st.EventsByApplication, dataloadgen.WithWait(time.Millisecond))}
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), ctxKey{}, l)))
	})
}

func For(ctx context.Context) *Loaders {
	l, _ := ctx.Value(ctxKey{}).(*Loaders)
	return l
}
