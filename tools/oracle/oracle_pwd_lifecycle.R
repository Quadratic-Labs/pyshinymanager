#!/usr/bin/env Rscript
# Oracle differentiel pour le cycle de vie du mot de passe (backend SQLite du source).
# Cree une base, positionne .tok, puis joue une sequence deterministe et renvoie les valeurs
# observables (retours de fonctions + etat pwd_mngt). Les deviations D2 (fail-closed) ne sont
# PAS couvertes ici (chemins d'erreur) : elles sont testees cote Python uniquement.
#
# Usage : Rscript oracle_pwd_lifecycle.R <out.json>

suppressWarnings(suppressMessages({ library(jsonlite); library(scrypt) }))

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1) stop("Usage: Rscript oracle_pwd_lifecycle.R <out.json>")
out_file <- args[[1]]

this_dir <- dirname(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)))
if (length(this_dir) == 0 || !nzchar(this_dir)) this_dir <- "."
src <- normalizePath(file.path(this_dir, "..", "..", "..", "source", Sys.getenv("SM_SOURCE", "shinymanager-1.1.1.1")), mustWork = TRUE)
suppressWarnings(suppressMessages(pkgload::load_all(src, quiet = TRUE, export_all = TRUE)))

tmp <- tempfile(fileext = ".sqlite")
create_db(
  credentials_data = data.frame(user = "alice", password = "azerty", stringsAsFactors = FALSE),
  sqlite_path = tmp, passphrase = NULL
)
.tok$set_sqlite_path(tmp)
.tok$set_passphrase(NULL)
tok <- "tok-alice"
.tok$add(tok, list(user = "alice"))

res <- list()

# 1. Etat initial : pas de forcage.
res$force_initial <- is_force_chg_pwd(tok)

# 2. force_chg_pwd(TRUE) -> forcage actif.
force_chg_pwd("alice", TRUE)
res$force_after_true <- is_force_chg_pwd(tok)
pm <- read_db_decrypt(tmp, "pwd_mngt", NULL)
res$must_change_after_true <- pm$must_change[pm$user == "alice"]

# 3. update_pwd -> succes, forcage retombe, nouveau mot de passe verifiable.
res$update_result <- update_pwd("alice", "Newpass1")$result
res$force_after_update <- is_force_chg_pwd(tok)
cr <- read_db_decrypt(tmp, "credentials", NULL)
res$verify_new <- scrypt::verifyPassword(cr$password[cr$user == "alice"], "Newpass1")
pm2 <- read_db_decrypt(tmp, "pwd_mngt", NULL)
res$have_changed_after_update <- pm2$have_changed[pm2$user == "alice"]

# 4. check_new_pwd : meme mot de passe -> FALSE ; different -> TRUE.
res$check_same <- check_new_pwd("alice", "Newpass1")
res$check_diff <- check_new_pwd("alice", "Other123")

# 5. check_locked_account : n_wrong_pwd=0 -> FALSE ; force a 2 -> TRUE (limite 2).
res$locked_zero <- check_locked_account("alice", 2)
pm3 <- read_db_decrypt(tmp, "pwd_mngt", NULL)
pm3$n_wrong_pwd[pm3$user == "alice"] <- 2
write_db_encrypt(tmp, pm3, "pwd_mngt", NULL)
res$locked_two <- check_locked_account("alice", 2)

writeLines(toJSON(res, auto_unbox = TRUE, null = "null", na = "null", pretty = TRUE),
           out_file, useBytes = TRUE)
cat("oracle pwd_lifecycle ecrit ->", out_file, "\n")
