package graph

import (
	"github.com/kianbqh/fullstack-go-graphql/backend/internal/auth"
	"github.com/kianbqh/fullstack-go-graphql/backend/internal/store"
)

// Resolver is the root dependency container for gqlgen resolvers.
type Resolver struct {
	Store *store.Store
	Auth  auth.Manager
}
