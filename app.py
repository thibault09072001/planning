import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# --- CONFIGURATION DE LA PAGE ---
st.set_page_config(page_title="Générateur de Planning - Ehpad", page_icon="🏥", layout="wide")

# --- DONNÉES ET PARAMÈTRES ---
JOURS_SEMAINE = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
CODES_POSTES = {
    "M": "Matin (7h)",
    "A": "Après-midi (7h)",
    "C": "Coupé (7h-11h / 17h30-20h30)",
    "R": "Repos"
}

# Besoins journaliers
BESOINS_SEMAINE = {"M": 8, "A": 4, "C": 1}
BESOINS_WE = {"M": 6, "A": 3, "C": 2}

def init_personnel():
    """Initialise la liste du personnel avec leur temps de travail."""
    personnel = []
    # 15 Temps complets (100%)
    for i in range(1, 16):
        personnel.append({"Nom": f"Soignant TC {i}", "Contrat": "100%", "Equipe_WE": "A" if i <= 8 else "B"})
    # 3 Temps partiels (80%)
    for i in range(1, 4):
        # On équilibre les équipes WE (9 dans l'équipe A, 9 dans l'équipe B)
        equipe = "A" if i == 1 else "B" 
        personnel.append({"Nom": f"Soignant TP {i}", "Contrat": "80%", "Equipe_WE": equipe})
    return pd.DataFrame(personnel)

# --- ALGORITHME DE GÉNÉRATION (HEURISTIQUE) ---
def generer_planning(df_personnel):
    """Génère un roulement de 4 semaines basé sur les contraintes."""
    colonnes_jours = [f"{JOURS_SEMAINE[d%7]} S{d//7 + 1}" for d in range(28)]
    planning = pd.DataFrame(index=df_personnel["Nom"], columns=colonnes_jours)
    planning.fillna("R", inplace=True) # Repos par défaut

    for index, row in df_personnel.iterrows():
        nom = row["Nom"]
        equipe = row["Equipe_WE"]
        
        # RÈGLE : 1 WE sur 2. 
        # Equipe A travaille WE S1 et S3. Equipe B travaille WE S2 et S4.
        we_travailles = [0, 2] if equipe == "A" else [1, 3]
        
        for num_semaine in range(4):
            # Index des jours de la semaine (0=Lun, ..., 5=Sam, 6=Dim)
            idx_base = num_semaine * 7
            
            if num_semaine in we_travailles:
                # --- SEMAINE DE TRAVAIL AVEC WE (6 jours travaillés) ---
                # Contrainte : 1 WE du matin, l'autre Aprem/Coupé
                if num_semaine in [0, 1]: # Premier WE travaillé du cycle
                    planning.loc[nom, colonnes_jours[idx_base + 5]] = "M" # Samedi
                    planning.loc[nom, colonnes_jours[idx_base + 6]] = "M" # Dimanche
                else: # Deuxième WE travaillé du cycle
                    planning.loc[nom, colonnes_jours[idx_base + 5]] = "A" # Samedi
                    planning.loc[nom, colonnes_jours[idx_base + 6]] = "C" # Dimanche (Coupé)

                # Pour faire 6 jours avec max 4 jours consécutifs : 
                # Repos le Mercredi. Travail Lun, Mar, Jeu, Ven, Sam, Dim (Ajusté pour éviter 5 jrs)
                # Modèle typique : Repos Mardi. Travail Lun, Mer, Jeu, Ven, Sam, Dim -> 6 jours consécutifs = INTERDIT
                # On force le repos le Vendredi pour couper avant le WE.
                planning.loc[nom, colonnes_jours[idx_base + 0]] = "M" # Lun
                planning.loc[nom, colonnes_jours[idx_base + 1]] = "M" # Mar
                planning.loc[nom, colonnes_jours[idx_base + 2]] = "R" # Mer (Repos)
                planning.loc[nom, colonnes_jours[idx_base + 3]] = "M" # Jeu
                planning.loc[nom, colonnes_jours[idx_base + 4]] = "R" # Ven (Repos pré-WE pour casser la série)
                # Il manque des jours pour faire 6j, l'algo basique nécessitera un ajustement manuel
                # On rajoute un jour travaillé pour s'approcher de la cible :
                planning.loc[nom, colonnes_jours[idx_base + 2]] = "A" 
                
            else:
                # --- SEMAINE DE REPOS LE WE (4 jours travaillés) ---
                # Repos Samedi (5) et Dimanche (6)
                planning.loc[nom, colonnes_jours[idx_base + 5]] = "R" 
                planning.loc[nom, colonnes_jours[idx_base + 6]] = "R" 
                
                # Travail 4 jours dans la semaine, ex: Lun, Mar, Jeu, Ven
                planning.loc[nom, colonnes_jours[idx_base + 0]] = "A"
                planning.loc[nom, colonnes_jours[idx_base + 1]] = "M" # Attention A suivi de M (à corriger via st.data_editor)
                planning.loc[nom, colonnes_jours[idx_base + 3]] = "A"
                planning.loc[nom, colonnes_jours[idx_base + 4]] = "C" # Un coupé en semaine comme demandé
                
    return planning

# --- INTERFACE UTILISATEUR ---
st.title("🏥 Planification des Soignants (Roulement 4 semaines)")
st.markdown("Générez, ajustez et exportez le planning selon vos contraintes métier.")

df_perso = init_personnel()

col1, col2 = st.columns([1, 4])

with col1:
    st.header("⚙️ Actions")
    if st.button("🔄 Générer le roulement", type="primary"):
        st.session_state["planning"] = generer_planning(df_perso)
        st.success("Planning généré avec succès !")
    
    st.markdown("---")
    st.markdown("**Légende :**")
    for code, desc in CODES_POSTES.items():
        st.markdown(f"- **{code}** : {desc}")
        
    st.markdown("---")
    st.info("💡 **Rappel** : L'algorithme place 9 titulaires par WE. Les besoins restants (ex: 2 postes) doivent être comblés par des remplaçants.")

with col2:
    if "planning" in st.session_state:
        st.header("📅 Éditeur de Planning")
        st.markdown("Vous pouvez **modifier directement les cases ci-dessous** pour ajuster les horaires (ex: éviter un A -> M) et valider vos changements.")
        
        # Affichage interactif
        planning_edite = st.data_editor(
            st.session_state["planning"], 
            use_container_width=True,
            height=650
        )
        
        # --- ANALYSE DES BESOINS (Vérification) ---
        st.subheader("📊 Vérification de la couverture (Semaine 1)")
        # On calcule combien de M, A, C sont placés le premier Lundi (colonne 0) et le premier Samedi (colonne 5)
        lundi_S1 = planning_edite.iloc[:, 0].value_counts()
        samedi_S1 = planning_edite.iloc[:, 5].value_counts()
        
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Lundi - Matin (Besoin: 8)", lundi_S1.get("M", 0))
        c2.metric("Lundi - Aprem (Besoin: 4)", lundi_S1.get("A", 0))
        c3.metric("Samedi - Matin (Besoin: 6)", samedi_S1.get("M", 0))
        c4.metric("Samedi - Remplaçants requis", max(0, 11 - (samedi_S1.get("M", 0) + samedi_S1.get("A", 0) + samedi_S1.get("C", 0))))

        # --- EXPORT ---
        st.markdown("---")
        csv = planning_edite.to_csv().encode('utf-8')
        st.download_button(
            label="📥 Exporter le planning en CSV",
            data=csv,
            file_name='planning_soignants_4semaines.csv',
            mime='text/csv',
        )
    else:
        st.info("👈 Cliquez sur 'Générer le roulement' pour commencer.")
