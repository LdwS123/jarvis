/* Le petit programme qui est « Jervis.app ».
 *
 * Il retrouve le dossier du projet (Jervis.app est rangé dedans), envoie tout ce qui s'affiche
 * dans jervis.log, et lance Python en processus *enfant* (posix_spawn, pas exec) : macOS
 * attribue alors les autorisations (micro, écran, Automatisation) à « Jervis » et non à « Python ».
 */
#include <fcntl.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;
static pid_t enfant = 0;

static void relayer(int sig) {
    if (enfant > 0) kill(enfant, sig);
}

int main(void) {
    char brut[PATH_MAX], chemin[PATH_MAX];
    uint32_t taille = sizeof brut;
    if (_NSGetExecutablePath(brut, &taille) != 0 || !realpath(brut, chemin)) return 1;

    /* …/Jervis/Jervis.app/Contents/MacOS/Jervis → on remonte de 4 crans jusqu'à …/Jervis */
    for (int i = 0; i < 4; i++) {
        char *barre = strrchr(chemin, '/');
        if (!barre) return 1;
        *barre = '\0';
    }
    if (chdir(chemin) != 0) return 1;

    int journal = open("jervis.log", O_WRONLY | O_CREAT | O_APPEND, 0644);
    if (journal >= 0) {
        dup2(journal, STDOUT_FILENO);
        dup2(journal, STDERR_FILENO);
        close(journal);
    }
    setenv("PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin", 1);
    setenv("PYTHONUNBUFFERED", "1", 1);
    setenv("PYTHONIOENCODING", "utf-8", 1);  /* les émojis du journal */
    setenv("LANG", "fr_FR.UTF-8", 0);

    char *args[] = {".venv/bin/python", "-m", "jervis", "--barre", NULL};
    if (posix_spawn(&enfant, args[0], NULL, NULL, args, environ) != 0) {
        perror("Jervis : impossible de lancer .venv/bin/python");
        return 1;
    }
    signal(SIGTERM, relayer);
    signal(SIGINT, relayer);

    int statut = 0;
    while (waitpid(enfant, &statut, 0) < 0) {
    }
    return WIFEXITED(statut) ? WEXITSTATUS(statut) : 1;
}
