#!/usr/bin/env Rscript
# Oracle differentiel du reset de mot de passe en libre-service (1.1.1.1, backend SQLite).
# Joue une sequence deterministe inspiree de tests/testthat/test-reset-password.R et renvoie
# les valeurs observables (codes reason, mail envoye ou non, mot de passe effectif, must_change,
# expiration, statuts de logs). Le mot de passe temporaire etant aleatoire, on compare des
# proprietes (verifiable, inchange, format de l'expiration), pas des valeurs.
#
# Usage : Rscript oracle_reset_password.R <out.json>

suppressWarnings(suppressMessages({ library(jsonlite); library(scrypt) }))

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1) stop("Usage: Rscript oracle_reset_password.R <out.json>")
out_file <- args[[1]]

this_dir <- dirname(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)))
if (length(this_dir) == 0 || !nzchar(this_dir)) this_dir <- "."
src <- normalizePath(file.path(this_dir, "..", "..", "..", "source", Sys.getenv("SM_SOURCE", "shinymanager-1.1.1.1")), mustWork = TRUE)
suppressWarnings(suppressMessages(pkgload::load_all(src, quiet = TRUE, export_all = TRUE)))

new_db <- function() {
  path <- tempfile(fileext = ".sqlite")
  create_db(
    credentials_data = data.frame(
      user = c("fanny", "victor", "bob"),
      password = c("azerty12", "12345A", "bobpwd1"),
      email = c("fanny@mail.com", "victor@mail.com", NA),
      stringsAsFactors = FALSE
    ),
    sqlite_path = path, passphrase = NULL
  )
  .tok$set_sqlite_path(path)
  .tok$set_passphrase(NULL)
  .tok$set_sql_config_db(NULL)
  path
}

captured <- new.env()
mail_ok <- function(user, email, temp_password) {
  captured$called <- captured$called + 1L
  captured$email <- email
  captured$pwd <- temp_password
  invisible(TRUE)
}
mail_ko <- function(user, email, temp_password) stop("smtp down")

# Lance un reset et renvoie les observables.
run <- function(user, email = NULL) {
  captured$called <- 0L
  captured$email <- NULL
  captured$pwd <- NULL
  r <- reset_pwd_user_email(user, email)
  list(result = r$result, reason = r$reason, mail_called = captured$called,
       mail_email = captured$email)
}
pwd_of <- function(path, user) {
  cr <- read_db_decrypt(path, "credentials", NULL)
  cr$password[cr$user == user]
}
pm_of <- function(path, user) {
  pm <- read_db_decrypt(path, "pwd_mngt", NULL)
  pm[pm$user == user, , drop = FALSE]
}

res <- list()
options(shinymanager.write_logs = FALSE)
path <- new_db()

# 1. Pas de fonction d'envoi.
.tok$set_send_mail(NULL)
options(shinymanager.reset_password = TRUE)
res$no_mailer <- run("fanny", "fanny@mail.com")

# 2-3. Saisies vides.
.tok$set_send_mail(mail_ok)
res$empty_user <- run("  ", "fanny@mail.com")
res$empty_email <- run("fanny", "")

# 4. Succes (email trim + casse ignoree), sans option de validite.
res$success <- run("fanny", "  FANNY@Mail.com ")
res$success_verify <- scrypt::verifyPassword(pwd_of(path, "fanny"), captured$pwd)
res$success_must_change <- isTRUE(as.logical(pm_of(path, "fanny")$must_change))
res$success_has_expire_col <- "temp_pwd_expire" %in% colnames(read_db_decrypt(path, "pwd_mngt", NULL))
fanny_pwd <- captured$pwd

# 5-6. Mauvais email, utilisateur inconnu.
res$wrong_email <- run("fanny", "other@mail.com")
res$unknown_user <- run("zoe", "zoe@mail.com")

# 7. Echec d'envoi : mot de passe inchange.
.tok$set_send_mail(mail_ko)
res$mail_failed <- run("fanny", "fanny@mail.com")
res$mail_failed_pwd_unchanged <- scrypt::verifyPassword(pwd_of(path, "fanny"), fanny_pwd)
.tok$set_send_mail(mail_ok)

# 8-9. Mode "username".
options(shinymanager.reset_password = "username")
res$username_no_email <- run("bob")
res$username_success <- run("victor")
res$username_verify <- scrypt::verifyPassword(pwd_of(path, "victor"), captured$pwd)

# 10. Validite 20 min : expiration posee, au format attendu, ~ maintenant + 20 min.
options(shinymanager.reset_password = TRUE, shinymanager.reset_password_validity = 20)
res$validity_success <- run("fanny", "fanny@mail.com")
exp <- pm_of(path, "fanny")$temp_pwd_expire
res$validity_format <- grepl("^\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2}$", exp)
delta <- as.numeric(difftime(as.POSIXct(exp, tz = "UTC"), Sys.time(), units = "mins"))
res$validity_delta_ok <- delta > 19 && delta <= 20
res$validity_other_users_empty <- all(read_db_decrypt(path, "pwd_mngt", NULL)$temp_pwd_expire[c(2, 3)] == "")
res$expired_now <- is_temp_pwd_expired("fanny")

# 11. Expiration passee -> expire.
set_temp_pwd_expire("fanny", "2000-01-01 00:00:00")
res$expired_past <- is_temp_pwd_expired("fanny")

# 12. Changement de mot de passe : expiration effacee.
res$update_result <- update_pwd("fanny", "Newpass1")$result
res$expire_after_update <- pm_of(path, "fanny")$temp_pwd_expire
res$expired_after_update <- is_temp_pwd_expired("fanny")

# 13. Valeurs d'option de validite invalides -> NA.
options(shinymanager.reset_password_validity = "abc")
res$validity_abc_na <- is.na(get_reset_password_validity())
options(shinymanager.reset_password_validity = 0)
res$validity_zero_na <- is.na(get_reset_password_validity())
options(shinymanager.reset_password_validity = "15")
res$validity_str15 <- get_reset_password_validity()
options(shinymanager.reset_password_validity = NULL)

# 14. Colonne email personnalisee absente.
options(shinymanager.email_column = "mail")
res$no_email_column <- run("fanny", "fanny@mail.com")
options(shinymanager.email_column = NULL)

# 15. Effacement sans colonne : rien n'est cree.
path2 <- new_db()
res$clear_without_col <- set_temp_pwd_expire("fanny", "")
res$clear_created_col <- "temp_pwd_expire" %in% colnames(read_db_decrypt(path2, "pwd_mngt", NULL))

# 16. Journalisation : statut par reason, rien si write_logs = FALSE.
save_reset_logs("u0", "success")
res$logs_when_disabled <- nrow(read_db_decrypt(path2, "logs", NULL))
options(shinymanager.write_logs = TRUE)
for (r in c("success", "unknown_user", "email_mismatch", "no_stored_email",
            "mail_failed", "empty_input", "no_mailer", "db_error")) {
  save_reset_logs(paste0("u_", r), r)
}
lg <- read_db_decrypt(path2, "logs", NULL)
res$logs_users <- lg$user
res$logs_status <- lg$status

writeLines(toJSON(res, auto_unbox = TRUE, null = "null", na = "null", pretty = TRUE),
           out_file, useBytes = TRUE)
cat("oracle reset_password ecrit ->", out_file, "\n")
