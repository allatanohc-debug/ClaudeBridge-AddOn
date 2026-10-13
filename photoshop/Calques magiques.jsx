// Calques magiques - entree du menu Fichier > Scripts de Photoshop.
// Lance la fenetre Python rangee dans Documents\CalquesMagiques.
(function () {
    var bat = new File(Folder.myDocuments + "/CalquesMagiques/Lancer Calques magiques.bat");
    if (!bat.exists) {
        alert("Calques magiques introuvable.\nRange le dossier « CalquesMagiques » dans tes Documents :\n" +
              Folder.myDocuments.fsName + "\\CalquesMagiques");
        return;
    }
    bat.execute();
})();
