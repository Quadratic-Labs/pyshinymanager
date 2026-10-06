#!/usr/bin/env Rscript
# Oracle differentiel pour la journalisation (backend SQLite). Joue une sequence deterministe
# (echec Wrong pwd -> increment, succes -> reset + log, logout -> tampon) et renvoie l'etat
# observable des tables logs/pwd_mngt.
#
# Usage : Rscript oracle_logs.R <out.json>

suppressWarnings(suppressMessages({ library(jsonlite); library(scrypt) }))

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1) stop("Usage: Rscript oracle_logs.R <out.json>")
out_file <- args[[1]]

this_dir <- dirname(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)))
if (length(this_dir) == 0 || !nzchar(this_dir)) this_dir <- "."
src <- normalizePath(file.path(this_dir, "..", "..", "..", "source", Sys.getenv("SM_SOURCE", "shinymanager-1.1.1.1")), mustWork = TRUE)
suppressWarnings(suppressMessages(pkgload::load_all(src, quiet = TRUE, export_all = TRUE)))

options(shinymanager.application = "myapp")
tmp <- tempfile(fileext = ".sqlite")
create_db(data.frame(user = "alice", password = "azerty", stringsAsFactors = FALSE), tmp, NULL)
.tok$set_sqlite_path(tmp)
.tok$set_passphrase(NULL)
tok <- "tok-alice"
.tok$add(tok, list(user = "alice"))

nwp <- function() {
  pm <- read_db_decrypt(tmp, "pwd_mngt", NULL)
  pm$n_wrong_pwd[pm$user == "alice"]
}

res <- list()

# 2 echecs Wrong pwd -> n_wrong_pwd = 2
save_logs_failed("alice", "Wrong pwd")
save_logs_failed("alice", "Wrong pwd")
res$nwp_after_two_fail <- nwp()

# echec non-Wrong-pwd -> pas d'increment
save_logs_failed("alice", "Unknown user")
res$nwp_after_unknown <- nwp()

# succes -> reset a 0 + 1 ligne Success
save_logs(tok)
res$nwp_after_success <- nwp()
logs <- read_db_decrypt(tmp, "logs", NULL)
res$n_success <- sum(logs$status == "Success")
res$n_wrong <- sum(logs$status == "Wrong pwd")

# save_logs idempotent (meme token/jour) -> toujours 1 Success
save_logs(tok)
logs2 <- read_db_decrypt(tmp, "logs", NULL)
res$n_success_after_replay <- sum(logs2$status == "Success")

# logout -> tampon logout non nul sur la ligne du token
logout_logs(tok)
logs3 <- read_db_decrypt(tmp, "logs", NULL)
res$logout_set <- any(!is.na(logs3$logout[logs3$token %in% tok]))

writeLines(toJSON(res, auto_unbox = TRUE, null = "null", na = "null", pretty = TRUE),
           out_file, useBytes = TRUE)
cat("oracle logs ecrit ->", out_file, "\n")
