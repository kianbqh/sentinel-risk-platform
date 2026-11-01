// Package auth: bcrypt password hashing, HS256 JWTs and an HTTP middleware that puts the user ID in the context.
package auth

import (
	"context"
	"errors"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"golang.org/x/crypto/bcrypt"
)

type ctxKey struct{}

var ErrUnauthenticated = errors.New("unauthenticated")

type Manager struct {
	Secret []byte
	TTL    time.Duration
	Cost   int // bcrypt cost
}

func (m Manager) Hash(pw string) (string, error) {
	if len(pw) < 8 {
		return "", errors.New("password must be at least 8 characters")
	}
	b, err := bcrypt.GenerateFromPassword([]byte(pw), m.Cost)
	return string(b), err
}

func (m Manager) Check(hash, pw string) bool {
	return bcrypt.CompareHashAndPassword([]byte(hash), []byte(pw)) == nil
}

func (m Manager) Issue(userID int64) (string, error) {
	now := time.Now()
	return jwt.NewWithClaims(jwt.SigningMethodHS256, jwt.RegisteredClaims{
		Subject:   strconv.FormatInt(userID, 10),
		IssuedAt:  jwt.NewNumericDate(now),
		ExpiresAt: jwt.NewNumericDate(now.Add(m.TTL)),
	}).SignedString(m.Secret)
}

func (m Manager) Parse(tok string) (int64, error) {
	var c jwt.RegisteredClaims
	_, err := jwt.ParseWithClaims(tok, &c, func(t *jwt.Token) (any, error) { return m.Secret, nil },
		jwt.WithValidMethods([]string{"HS256"}), jwt.WithExpirationRequired())
	if err != nil {
		return 0, ErrUnauthenticated
	}
	return strconv.ParseInt(c.Subject, 10, 64)
}

// Middleware attaches the user ID for a valid "Authorization: Bearer <jwt>" header. Requests without a token pass
// through (register/login are public); resolvers that need a user call UserID.
func (m Manager) Middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if h := r.Header.Get("Authorization"); strings.HasPrefix(h, "Bearer ") {
			id, err := m.Parse(strings.TrimPrefix(h, "Bearer "))
			if err != nil {
				http.Error(w, `{"errors":[{"message":"invalid token"}]}`, http.StatusUnauthorized)
				return
			}
			r = r.WithContext(context.WithValue(r.Context(), ctxKey{}, id))
		}
		next.ServeHTTP(w, r)
	})
}

func UserID(ctx context.Context) (int64, error) {
	if id, ok := ctx.Value(ctxKey{}).(int64); ok {
		return id, nil
	}
	return 0, ErrUnauthenticated
}
