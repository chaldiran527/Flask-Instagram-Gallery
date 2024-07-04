from flask import Flask, render_template, request, redirect, send_from_directory, flash, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy 
from flask_login import LoginManager, UserMixin, login_user, logout_user
import requests
import subprocess
import time 
import urllib.parse
import shutil
import zipfile
import instaloader
import os
import re 


app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'
app.secret_key = 'supersecretkey' 

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)

L = instaloader.Instaloader()

class File(db.Model):
    id = db.Column(db.Integer, primary_key=True)#The prime key
    filename = db.Column(db.String(200), nullable=False)#Obligatory field
    file_type = db.Column(db.String(10), nullable=False) 
    
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(250), unique=True, nullable=False)
    password = db.Column(db.String(250), nullable=False)

# Ensure the uploads folder exists
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])


#Apparently it creates the models 
with app.app_context():
    db.create_all()

#Check if session insta filename is present in the directory
def ensure_session_file(account):
    session_file = f"session-{account}"
    if not os.path.exists(session_file):
        try:
            # Run the 615_import_firefox_session.py script
            subprocess.run(["python", "615_import_firefox_session.py", account], check=True)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to run 615_import_firefox_session.py: {e}")

#route definitions
@app.route('/')
def index():
    return render_template('index.html')



#route that returns the image viewer menu
@app.route("/menu")
def view_menu():
    files = File.query.all() #get all instances of File db object
    return render_template('menu.html', files=files)

@login_manager.user_loader
def loader_user(user_id):
    return User.query.get(user_id)

@app.route('/register', methods=["GET", "POST"])
def register():
    if request.method == "POST":
        user = User(username=request.form.get("username"),
                     password=request.form.get("password"))
        db.session.add(user)
        db.session.commit()
        return redirect('/')
    return render_template("sign_up.html")
 

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        user = User.query.filter_by(username=username).first()
        if user and user.password == password:
            login_user(user)
            return redirect('/menu')
        else:
            flash('Invalid username or password', 'error')
    return render_template("index.html")
    

@app.route('/uploads', methods=['POST'])
def uploads():
    if request.method == 'POST':
        file = request.files['file']
        if file:
            filename = file.filename
            file_type = 'image' if file.content_type.startswith('image') else 'video' if file.content_type.startswith('video') else None
            if not file_type:
                flash('Unsupported file type.', 'error')
                return redirect('/menu')

            # Check if the file already exists in the database
            existing_file = File.query.filter_by(filename=filename).first()
            if existing_file:
                flash('File already exists.', 'error')
                return redirect('/menu')

            # Save the file to the uploads folder
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            new_file = File(filename=filename, file_type=file_type)
            db.session.add(new_file)
            db.session.commit()
            return redirect('/menu')
    return 'Error, try again please'


#route to show files
@app.route('/uploaded_file/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

#route to download files
@app.route('/download/<int:file_id>')
def download(file_id):
    file = File.query.get_or_404(file_id)
    return send_from_directory(app.config['UPLOAD_FOLDER'], file.filename, as_attachment=True)


@app.route('/delete/<int:file_id>')
def delete(file_id):
    file = File.query.get_or_404(file_id)
    filename = file.filename

    file_path = os.path.join(app.config['UPLOAD_FOLDER'],filename)
    os.remove(file_path)
    db.session.delete(file)
    db.session.commit()

#    return send_from_directory(app.config['UPLOAD_FOLDER'], file.filename, as_attachment=True)
    return redirect('/menu')


#route to get followers
@app.route('/get_posts/<username>/<account>')
def get_followers(username, account):
    ensure_session_file(account)
    L.load_session_from_file(account, filename=f'session-{account}')
    profile = instaloader.Profile.from_username(L.context, username)
    #main_followers = profile.followers
    posts = profile.get_posts()
    #Get posts and convert to list of dictionaries
    posts = profile.get_posts()
    posts_list = []
    for post in posts:
        posts_list.append({
            "shortcode": post.shortcode,
            "caption": post.caption,
            "url": post.url,
            "likes": post.likes,
            "comments": post.comments,
            "is_video": post.is_video
        })
    
    return jsonify(posts_list)


#Route to download and save profile picture
@app.route('/save_profile_pic/<username>/<account>')
def save_profile_pic(username, account):
    ensure_session_file(account)
    L.load_session_from_file(account, filename=f'session-{account}')
    profile = instaloader.Profile.from_username(L.context, username)
    
    try:
        # Set the download directory to the current directory
        L.dirname_pattern = "."

        # Record the list of files before download
        before_download = set(os.listdir("."))

        # Download profile picture using Instaloader
        L.download_profilepic(profile)

        # Record the list of files after download
        after_download = set(os.listdir("."))

        # Find the newly added file(s)
        new_files = after_download - before_download

        # Filter out the downloaded profile picture file
        downloaded_files = [f for f in new_files if f.endswith('.jpg')]
        
        if downloaded_files:
            # Get the most recent downloaded file
            original_filename = max(downloaded_files, key=lambda x: os.path.getctime(os.path.join(".", x)))
            file_type = 'image'

            #Verify if the file exists in the DB File table  
            existing_file = File.query.filter_by(filename=original_filename).first()
            if existing_file:
                os.remove(original_filename)
                return f"File {original_filename} already exists."


            # Save file to database
            new_file = File(filename=original_filename, file_type=file_type)
            db.session.add(new_file)
            db.session.commit()

            # Move the file to the uploads directory
            new_file_path = os.path.join(app.config['UPLOAD_FOLDER'], original_filename)
            shutil.move(original_filename, new_file_path)
            
            return f"Profile picture for {username} downloaded and saved successfully as {original_filename}."
        else:
            return f"Failed to find downloaded profile picture for {username}."
    except Exception as e:
        return f"Failed to save profile picture for {username}: {str(e)}"    

#Route to get curr stories
@app.route('/get_current_stories/<username>/<account>')
def get_current_stories(username, account):
    ensure_session_file(account)
    L.load_session_from_file(account, filename=f'session-{account}')
    profile = instaloader.Profile.from_username(L.context, username)
    #stories = profile.get_stories()
    stories = L.get_stories(userids=[profile.userid])
    stories_list = []
    for story in stories:
        for item in story.get_items():
            stories_list.append({
                "date_local": item.date_local,
                "item": item.is_video,
                "owner_username": item.owner_username,
                "url": item.url
            })
    return jsonify(stories_list)

# Route to download current stories
@app.route('/save_current_stories/<username>/<account>')
def save_current_stories(username, account):
    try:
        ensure_session_file(account)
        L.load_session_from_file(account, filename=f'session-{account}')
        profile = instaloader.Profile.from_username(L.context, username)

        # Fetch stories
        stories = L.get_stories(userids=[profile.userid])

        if not stories:
            return f"No stories found for {username}."

        # Record the list of files before download
        before_download = set(os.listdir("."))

        # Download each story item
        for story in stories:
            for item in story.get_items():
                try:
                    # Download video if the item is a video
                    if item.is_video:
                        video_filename = f"{item.date_utc.strftime('%Y-%m-%d_%H-%M-%S')}_UTC.mp4"
                        image_filename = f"{item.date_utc.strftime('%Y-%m-%d_%H-%M-%S')}_UTC.jpg"
                        
                        # Download video
                        response = requests.get(item.video_url, stream=True)
                        response.raise_for_status()
                        with open(video_filename, 'wb') as f:
                            f.write(response.content)
                        
                        # Download thumbnail image
                        response = requests.get(item.url, stream=True)
                        response.raise_for_status()
                        with open(image_filename, 'wb') as f:
                            f.write(response.content)
                    
                    # If the item is an image
                    else:
                        image_filename = f"{item.date_utc.strftime('%Y-%m-%d_%H-%M-%S')}_UTC.jpg"
                        
                        # Download image
                        response = requests.get(item.url, stream=True)
                        response.raise_for_status()
                        with open(image_filename, 'wb') as f:
                            f.write(response.content)
                
                except Exception as e:
                    return f"Failed to download story item: {str(e)}"

        # Record the list of files after download
        after_download = set(os.listdir("."))

        # Find the newly added files
        new_files = after_download - before_download

        # Filter out the downloaded story files
        downloaded_files = [f for f in new_files if f.endswith('.jpg') or f.endswith('.mp4')]

        if downloaded_files:
            for filename in downloaded_files:
                file_type = 'video' if filename.endswith('.mp4') else 'image'
                # Verify if the file exists in the DB File table
                existing_file = File.query.filter_by(filename=filename).first()
                if existing_file:
                    os.remove(filename)
                    continue  # Skip if file already exists

                # Save file to database
                new_file = File(filename=filename, file_type=file_type)
                db.session.add(new_file)
                db.session.commit()

                # Move the file to the uploads directory
                new_file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                shutil.move(filename, new_file_path)

            return f"Stories for {username} downloaded and saved successfully."
        else:
            return f"Failed to download and save stories for {username}."
    except Exception as e:
        return f"Failed to download and save stories for {username}: {str(e)}"


if __name__ == '__main__':
    app.run(debug=True)
